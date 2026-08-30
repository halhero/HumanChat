from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from human_chat.character import load_character
from human_chat.config import Settings, load_settings
from human_chat.logging_config import get_logger
from human_chat.llm import create_chat_model
from human_chat.memory_extractor import extract_memory_candidates
from human_chat.memory_review import (
    create_memory_review_request,
    parse_memory_review_decision,
    parse_memory_review_request,
)
from human_chat.memory_service import MemoryService
from human_chat.schemas import ChatState
from human_chat.tool_provider import ToolRegistry, create_tool_registry
from human_chat.tool_review import (
    create_tool_review_request,
    parse_tool_review_decision,
    redact_tool_arguments,
    tool_calls_require_confirmation,
)
from human_chat.tts import TtsClient, TtsError


logger = get_logger(__name__)


TOOL_CALLING_PROMPT = """
你可以使用当前注册的本地工具和 MCP 工具完成任务。
只有当用户问题确实需要外部信息或操作时才调用工具。
工具可能需要用户确认；系统会在执行前处理确认流程，不要绕过或虚构工具结果。
如果工具结果仍不足以回答，可以继续调用工具。
如果不需要工具，或者已经收集到足够信息，请直接给出简短结论，不要调用工具。
"""

TOOL_LIMIT_PROMPT = """
本轮工具调用已经达到上限。请停止调用工具，并根据当前对话和已经获得的工具结果，
直接给出最终回答。如果信息仍然不足，请明确说明缺少什么信息。
"""

MAX_TOOL_CALL_ROUNDS = 3


def _build_system_prompt(character, memory_prompt: str) -> str:
    return (
        f"{character.system_prompt}\n"
        f"以下是你应该长期记住的用户和项目背景：\n"
        f"{memory_prompt}\n"
        f"请使用角色配置指定的语言回复：{character.reply_language}。\n"
        f"{TOOL_CALLING_PROMPT}"
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


def _tool_call_name(tool_call) -> str:
    if isinstance(tool_call, dict):
        return tool_call.get("name", "unknown_tool")
    return getattr(tool_call, "name", "unknown_tool")


def _tool_call_args(tool_call):
    if isinstance(tool_call, dict):
        return tool_call.get("args", {})
    return getattr(tool_call, "args", {})


def _tool_call_id(tool_call) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("id", ""))
    return str(getattr(tool_call, "id", ""))


def _build_tool_events(state: ChatState, tool_result_messages: list) -> list[dict]:
    if not state.tool_messages:
        return []
    last_message = state.tool_messages[-1]
    tool_calls = getattr(last_message, "tool_calls", []) or []
    events = []
    for index, tool_call in enumerate(tool_calls):
        result_message = tool_result_messages[index] if index < len(tool_result_messages) else None
        content = str(getattr(result_message, "content", ""))
        message_status = getattr(result_message, "status", None)
        is_error = message_status == "error" or content.startswith("[tool_error]")
        events.append(
            {
                "round": state.tool_call_count + 1,
                "tool": _tool_call_name(tool_call),
                "arguments": redact_tool_arguments(_tool_call_args(tool_call)),
                "status": "error" if is_error else "success",
                "result_preview": content[:300],
            }
        )
    return events


def build_graph(
    settings: Settings | None = None,
    *,
    memory_service: MemoryService,
    checkpointer=None,
    tool_registry: ToolRegistry | None = None,
):
    settings = settings or load_settings()
    character = load_character(settings.character_path)
    llm = create_chat_model(settings)
    active_tool_registry = tool_registry or create_tool_registry()
    # Graph 只消费统一注册表中的 LangChain Tool，不区分本地实现和 MCP 来源。
    # 来源、安全策略和同步/异步适配都已在 Provider 层完成。
    project_tools = active_tool_registry.get_tools()
    tool_llm = llm.bind_tools(project_tools)
    tool_node = ToolNode(project_tools, messages_key="tool_messages")
    tts_client = TtsClient(settings, character)

    def prepare_context(state: ChatState):
        # 这些字段属于“当前一轮提问”的临时执行状态。每次新提问都清空审批结果，
        # 防止上一轮对某个工具的批准被错误复用于下一轮调用。
        return {
            "memory_prompt": memory_service.format_for_prompt(),
            "tool_messages": [],
            "tool_call_count": 0,
            "tool_events": [],
            "tool_limit_reached": False,
            "tool_review_request": None,
            "tool_review_approved": None,
            "memory_review_request": None,
            "memory_saved_count": 0,
        }

    def call_agent_model(state: ChatState):
        tool_messages = state.tool_messages or [
            HumanMessage(content=state.question)
        ]
        conversation = [
            SystemMessage(
                content=_build_system_prompt(character, state.memory_prompt)
            ),
            *state.messages,
            *tool_messages,
        ]

        response = tool_llm.invoke(conversation)
        return {"tool_messages": [*tool_messages, response]}

    def execute_project_tools(state: ChatState):
        # ToolNode 使用模型产生的原始 tool call，因此审批界面中的参数脱敏副本不会
        # 改写真正的调用参数。MCP 工具的同步入口已由 McpToolProvider 注入。
        result = tool_node.invoke({"tool_messages": state.tool_messages})
        tool_result_messages = result.get("tool_messages", [])
        return {
            "tool_messages": [*state.tool_messages, *tool_result_messages],
            "tool_call_count": state.tool_call_count + 1,
            "tool_events": [*state.tool_events, *_build_tool_events(state, tool_result_messages)],
            "tool_review_request": None,
            "tool_review_approved": None,
        }

    def review_tool_calls(state: ChatState):
        """暂停 Graph，等待调用方明确批准这一批高风险工具。"""

        last_message = state.tool_messages[-1]
        tool_calls = getattr(last_message, "tool_calls", []) or []
        review_request = create_tool_review_request(
            tool_calls,
            active_tool_registry,
        )
        # interrupt 会把状态持久化到 checkpointer，并把审批请求交还 CLI。恢复时
        # Command(resume=...) 的值会成为这里的返回值，然后 Graph 从本节点继续。
        decision_data = interrupt(
            {
                "type": "tool_review",
                "request": _model_to_dict(review_request),
            }
        )
        decision = parse_tool_review_decision(decision_data)
        return {
            "tool_review_request": _model_to_dict(review_request),
            "tool_review_approved": decision.approved,
        }

    def reject_tool_calls(state: ChatState):
        """在不执行工具的情况下，为每个被拒调用生成协议完整的结果消息。"""

        last_message = state.tool_messages[-1]
        tool_calls = getattr(last_message, "tool_calls", []) or []
        rejected_messages = []
        rejected_events = []

        for tool_call in tool_calls:
            name = _tool_call_name(tool_call)
            # OpenAI/LangChain 工具协议要求每个 tool_call_id 都对应一个 ToolMessage。
            # 仅跳过 ToolNode 会留下悬空调用，后续模型请求可能因此被服务端拒绝。
            rejected_messages.append(
                ToolMessage(
                    content="[tool_denied] 用户拒绝了本次工具调用。",
                    tool_call_id=_tool_call_id(tool_call),
                    name=name,
                    status="error",
                )
            )
            rejected_events.append(
                {
                    "round": state.tool_call_count + 1,
                    "tool": name,
                    "arguments": redact_tool_arguments(_tool_call_args(tool_call)),
                    "status": "denied",
                    "result_preview": "用户拒绝了本次工具调用。",
                }
            )

        return {
            "tool_messages": [*state.tool_messages, *rejected_messages],
            "tool_call_count": state.tool_call_count + 1,
            "tool_events": [*state.tool_events, *rejected_events],
            "tool_review_request": None,
            "tool_review_approved": None,
        }

    def generate_limit_reply(state: ChatState):
        conversation = [
            SystemMessage(
                content=_build_system_prompt(character, state.memory_prompt)
            ),
            *state.messages,
            *state.tool_messages,
            SystemMessage(content=TOOL_LIMIT_PROMPT),
        ]
        response = llm.invoke(conversation)
        return {"tool_messages": [*state.tool_messages, response]}

    def finalize_reply(state: ChatState):
        if not state.tool_messages:
            raise RuntimeError("模型调用结束，但没有返回任何消息。")

        response = state.tool_messages[-1]
        assistant_text = _message_text(response)
        if not assistant_text:
            raise RuntimeError("模型没有返回可显示的文本回答。")

        logger.info("Generated assistant reply")
        return {
            "assistant_text": assistant_text,
            "tts_error": "",
            "messages": [
                HumanMessage(content=state.question),
                AIMessage(content=assistant_text),
            ],
        }

    def synthesize_speech(state: ChatState):
        try:
            tts_client.synthesize_and_play(state.assistant_text)
        except TtsError as exc:
            logger.warning("TTS failed: %s", exc)
            return {"tts_error": str(exc)}
        return {"tts_error": ""}

    def extract_memory(state: ChatState):
        if not settings.memory_extraction_enabled or not state.assistant_text:
            return {"memory_review_request": None}

        try:
            candidates = extract_memory_candidates(llm, state.question, state.assistant_text)
        except Exception:
            logger.exception("Failed to extract memory candidates")
            return {"memory_review_request": None}

        review_request = create_memory_review_request(candidates)
        if not review_request.candidates:
            return {"memory_review_request": None}
        return {"memory_review_request": _model_to_dict(review_request)}

    def review_memory(state: ChatState):
        review_request = parse_memory_review_request(state.memory_review_request)
        if not review_request.candidates:
            return {"memory_saved_count": 0}

        decision_data = interrupt(
            {
                "type": "memory_review",
                "request": _model_to_dict(review_request),
            }
        )
        decision = parse_memory_review_decision(decision_data)
        saved_count = 0

        for text in decision.accepted_texts:
            if memory_service.add(text, source="extracted_confirmed"):
                saved_count += 1

        return {"memory_saved_count": saved_count}

    def mark_tool_limit_reached(state: ChatState):
        return {
            "tool_limit_reached": True,
            "tool_events": [
                *state.tool_events,
                {
                    "round": state.tool_call_count,
                    "tool": "tool_loop",
                    "arguments": {},
                    "status": "limit_reached",
                    "result_preview": f"达到最大工具调用轮数：{MAX_TOOL_CALL_ROUNDS}",
                },
            ],
        }

    def route_after_agent_model(state: ChatState):
        if not state.tool_messages:
            return "finalize_reply"
        last_message = state.tool_messages[-1]
        if getattr(last_message, "tool_calls", None):
            if state.tool_call_count >= MAX_TOOL_CALL_ROUNDS:
                return "mark_tool_limit_reached"
            # 一个模型回复可以同时请求多个工具。只要其中一个受保护，就先审批
            # 整个批次，避免可写调用夹在只读调用中绕过人工确认。
            if tool_calls_require_confirmation(
                last_message.tool_calls,
                active_tool_registry,
            ):
                return "review_tool_calls"
            return "execute_project_tools"
        return "finalize_reply"

    def route_after_tool_review(state: ChatState):
        # 审批只有两个出口：批准才进入真实 ToolNode；其他值包括缺失值都拒绝。
        if state.tool_review_approved:
            return "execute_project_tools"
        return "reject_tool_calls"

    workflow = StateGraph(ChatState)
    workflow.add_node("prepare_context", prepare_context)
    workflow.add_node("call_agent_model", call_agent_model)
    workflow.add_node("execute_project_tools", execute_project_tools)
    workflow.add_node("review_tool_calls", review_tool_calls)
    workflow.add_node("reject_tool_calls", reject_tool_calls)
    workflow.add_node("generate_limit_reply", generate_limit_reply)
    workflow.add_node("finalize_reply", finalize_reply)
    workflow.add_node("extract_memory", extract_memory)
    workflow.add_node("review_memory", review_memory)
    workflow.add_node("synthesize_speech", synthesize_speech)
    workflow.add_node("mark_tool_limit_reached", mark_tool_limit_reached)
    workflow.add_edge(START, "prepare_context")
    workflow.add_edge("prepare_context", "call_agent_model")
    workflow.add_conditional_edges(
        "call_agent_model",
        route_after_agent_model,
        {
            "execute_project_tools": "execute_project_tools",
            "review_tool_calls": "review_tool_calls",
            "mark_tool_limit_reached": "mark_tool_limit_reached",
            "finalize_reply": "finalize_reply",
        },
    )
    # 工具审批是 Graph 的一等节点，而不是 CLI 在 Graph 外自行调用工具。这样暂停、
    # 恢复和 checkpoint 都保持在 LangGraph 的状态机语义内。
    workflow.add_conditional_edges(
        "review_tool_calls",
        route_after_tool_review,
        {
            "execute_project_tools": "execute_project_tools",
            "reject_tool_calls": "reject_tool_calls",
        },
    )
    workflow.add_edge("execute_project_tools", "call_agent_model")
    workflow.add_edge("reject_tool_calls", "call_agent_model")
    workflow.add_edge("mark_tool_limit_reached", "generate_limit_reply")
    workflow.add_edge("generate_limit_reply", "finalize_reply")
    workflow.add_edge("finalize_reply", "synthesize_speech")
    workflow.add_edge("synthesize_speech", "extract_memory")
    workflow.add_edge("extract_memory", "review_memory")
    workflow.add_edge("review_memory", END)
    return workflow.compile(checkpointer=checkpointer)


def _model_to_dict(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
