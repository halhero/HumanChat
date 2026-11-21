import json

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from human_chat.character import Character, load_character
from human_chat.config import Settings, load_settings
from human_chat.logging_config import get_logger
from human_chat.llm import create_chat_model
from human_chat.memory.retrieval import MemoryRetriever
from human_chat.memory.request import assemble_request, context_references, recent_history
from human_chat.memory.service import MemoryService
from human_chat.messages import SourceMessage, new_source
from human_chat.request_understanding import invoke_request_understanding
from human_chat.schemas import ChatState
from human_chat.tool_provider import ToolRegistry, create_tool_registry


logger = get_logger(__name__)


TOOL_CALLING_PROMPT = """
你可以使用当前注册的本地工具和 MCP 工具完成任务。
只有当用户问题确实需要外部信息或操作时才调用工具。
工具可能需要用户确认；系统会在执行前处理确认流程，不要绕过或虚构工具结果。
如果工具结果仍不足以回答，可以继续调用工具。
如果不需要工具，或者已经收集到足够信息，请直接给出简短结论，不要调用工具。
"""


def _project_tool_interrupts(registry: ToolRegistry) -> dict[str, dict]:
    return {
        registration.name: {"allowed_decisions": ["approve", "reject"]}
        for registration in registry.registrations()
        if registration.policy.requires_confirmation
    }

def _build_system_prompt(character, resolved_request: str) -> str:
    base = (
        f"{character.system_prompt}\n"
        f"请使用角色配置指定的语言回复：{character.reply_language}。\n"
        f"{TOOL_CALLING_PROMPT}"
    )
    if not resolved_request:
        return base
    derived = json.dumps(
        {"resolved_request": resolved_request},
        ensure_ascii=False,
    )
    return (
        base
        + "\n以下 JSON 是根据本回合原始用户消息和近期对话得到的派生语义视图，"
        "用于消解指代和省略，不是第二条用户指令。若它与原始用户消息冲突，"
        "必须以原始用户消息为准：\n"
        + derived
    )


def _message_text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content).strip()

    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if isinstance(block, dict):
            text = block.get("text")
            if text is not None:
                parts.append(str(text))
                continue
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
    return "".join(parts).strip()


def build_graph(
    settings: Settings | None = None,
    *,
    memory_service: MemoryService,
    memory_retriever: MemoryRetriever,
    context_builder,
    checkpointer=None,
    tool_registry: ToolRegistry | None = None,
    character: Character | None = None,
):
    settings = settings or load_settings()
    active_character = character or load_character(settings.character_path)
    llm = create_chat_model(settings)
    active_tool_registry = tool_registry or create_tool_registry()
    # Graph 只消费统一注册表中的 LangChain Tool，不区分本地实现和 MCP 来源。
    # 来源、安全策略和同步/异步适配都已在 Provider 层完成。
    project_tools = active_tool_registry.get_tools()
    react_agent = create_agent(
        model=llm,
        tools=project_tools,
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on=_project_tool_interrupts(active_tool_registry),
            ),
            ToolCallLimitMiddleware(
                run_limit=settings.react_tool_call_limit,
                exit_behavior="continue",
            ),
        ],
        name="human_chat_react",
    )

    def initialize_turn(state: ChatState, config: RunnableConfig):
        source = SourceMessage.model_validate(state.input_source) if state.input_source else None
        if source is None or source.content != state.question or any(m.id == source.id for m in state.messages):
            source = new_source(state.question, user_id=settings.memory_user_id,
                                session_id=str(config.get("configurable", {}).get("thread_id", "standalone")))
        if source.user_id != settings.memory_user_id or source.role != "user":
            raise ValueError("Input source does not match the configured user")
        # 这些字段属于“当前一轮提问”的临时执行状态。每次新提问都清空审批结果，
        # 防止上一轮对某个工具的批准被错误复用于下一轮调用。
        return {
            "input_source": source.model_dump(mode="json"),
            "resolved_request": "",
            "clarification_question": "",
            "execution_mode": "react",
            "memory_context": {},
            "assistant_text": "",
            "remaining_steps": [],
            "step_history": [],
            "authorized_step_limit": 0,
            "plan_user_context": [],
            "step_used_ask_user": False,
            "plan_end_reason": None,
        }

    def _build_recent_context_payload(state: ChatState) -> list[dict[str, str]]:
        recent = recent_history(state.messages, settings.memory_history_tokens)
        return [
            {"role": message.type, "content": _message_text(message)}
            for message in recent
        ]

    def understand_request(state: ChatState):
        result = invoke_request_understanding(
            llm,
            memory_retriever,
            state.question,
            _build_recent_context_payload(state),
        )
        return {
            "resolved_request": result.resolved_request,
            "clarification_question": result.clarification_question,
            "execution_mode": result.execution_mode,
        }

    def build_memory_context(state: ChatState):
        memory_context = context_references(
            context_builder.build(
                state.resolved_request,
                _build_recent_context_payload(state),
            )
        )
        return {"memory_context": memory_context}

    def reply_clarification(state: ChatState):
        return {"assistant_text": state.clarification_question}

    def run_react_agent(state: ChatState, config: RunnableConfig):
        current = [
            SourceMessage.model_validate(state.input_source).to_message()
        ]
        conversation = build_request(state, current, project_tools)
        result = react_agent.invoke(
            {"messages": conversation},
            config=config,
        )
        assistant_text = _message_text(result["messages"][-1])
        if not assistant_text:
            raise RuntimeError("Agent did not return a user-facing reply")
        return {"assistant_text": assistant_text}

    def build_request(state, current, tools):
        conversation, diagnostics = assemble_request(_build_system_prompt(active_character, state.resolved_request), state.messages,
            current, state.memory_context, memory_service, tools, settings)
        if diagnostics:
            logger.info("Memory context diagnostics: %s", diagnostics)
        return conversation

    def finalize_reply(state: ChatState):
        assistant_text = state.assistant_text.strip()
        if not assistant_text:
            raise RuntimeError("模型没有返回可显示的文本回答。")

        logger.info("Generated assistant reply")
        return {
            "assistant_text": assistant_text,
            "messages": [
                SourceMessage.model_validate(state.input_source).to_message(),
                new_source(assistant_text, user_id=settings.memory_user_id,
                           session_id=state.input_source["session_id"], role="assistant").to_message(),
            ],
        }

    def finish_turn(state: ChatState):
        return {}

    def route_after_request_understanding(state: ChatState):
        if state.clarification_question:
            return "reply_clarification"
        return "build_memory_context"

    workflow = StateGraph(ChatState)
    workflow.add_node("initialize_turn", initialize_turn)
    workflow.add_node("understand_request", understand_request)
    workflow.add_node("build_memory_context", build_memory_context)
    workflow.add_node("reply_clarification", reply_clarification)
    workflow.add_node("run_react_agent", run_react_agent)
    workflow.add_node("finalize_reply", finalize_reply)
    workflow.add_node("finish_turn", finish_turn)
    workflow.add_edge(START, "initialize_turn")
    workflow.add_edge("initialize_turn", "understand_request")
    workflow.add_conditional_edges(
        "understand_request",
        route_after_request_understanding,
        {
            "build_memory_context": "build_memory_context",
            "reply_clarification": "reply_clarification",
        },
    )
    workflow.add_edge("build_memory_context", "run_react_agent")
    workflow.add_edge("reply_clarification", "finalize_reply")
    workflow.add_edge("run_react_agent", "finalize_reply")
    workflow.add_edge("finalize_reply", "finish_turn")
    workflow.add_edge("finish_turn", END)
    return workflow.compile(checkpointer=checkpointer)
