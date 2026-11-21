import json
from typing import Literal

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from human_chat.character import Character, load_character
from human_chat.config import Settings, load_settings
from human_chat.execution import (
    CREATE_PLAN_PROMPT,
    PLAN_STEP_PROMPT,
    REPLAN_PROMPT,
    SUMMARY_PLAN_PROMPT,
    ExecutionPlan,
    ReplanDecision,
    StepExecutionRecord,
    StepResultSubmission,
    ask_user,
)
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


def _tool_capabilities(registry: ToolRegistry) -> list[dict]:
    return [
        {
            "name": registration.name,
            "description": registration.description or "",
            "requires_confirmation": registration.policy.requires_confirmation,
        }
        for registration in registry.registrations()
    ]


def _structured_result(result, schema):
    return result if isinstance(result, schema) else schema.model_validate(result)

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
    plan_step_agent = create_agent(
        model=llm,
        tools=[*project_tools, ask_user],
        response_format=StepResultSubmission,
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    **_project_tool_interrupts(active_tool_registry),
                    "ask_user": {
                        "allowed_decisions": ["respond", "reject"],
                    },
                },
            ),
            ToolCallLimitMiddleware(
                run_limit=settings.plan_step_tool_call_limit,
                exit_behavior="error",
            ),
        ],
        name="human_chat_plan_step",
    )
    tool_capabilities = _tool_capabilities(active_tool_registry)

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

    def create_plan(state: ChatState):
        result = llm.with_structured_output(ExecutionPlan).invoke([
            SystemMessage(content=CREATE_PLAN_PROMPT),
            HumanMessage(content=json.dumps({
                "resolved_request": state.resolved_request,
                "memory_context": state.memory_context,
                "tool_capabilities": tool_capabilities,
                "max_steps": settings.plan_initial_max_steps,
            }, ensure_ascii=False)),
        ])
        plan = _structured_result(result, ExecutionPlan)
        if len(plan.steps) > settings.plan_initial_max_steps:
            raise ValueError(
                "planner returned more steps than plan_initial_max_steps"
            )
        return {
            "remaining_steps": plan.steps,
            "step_history": [],
            "authorized_step_limit": settings.plan_initial_max_steps,
            "plan_user_context": [],
            "step_used_ask_user": False,
            "plan_end_reason": None,
        }

    def before_plan_step(
        state: ChatState,
    ) -> Command[Literal[
        "mark_plan_hard_limit",
        "mark_plan_completed",
        "review_plan_budget",
        "execute_plan_step",
    ]]:
        if len(state.step_history) >= settings.plan_hard_execution_limit:
            return Command(goto="mark_plan_hard_limit")
        if not state.remaining_steps:
            return Command(goto="mark_plan_completed")
        if len(state.step_history) >= state.authorized_step_limit:
            return Command(goto="review_plan_budget")
        return Command(goto="execute_plan_step")

    def mark_plan_hard_limit(state: ChatState):
        return {"plan_end_reason": "hard_limit"}

    def mark_plan_completed(state: ChatState):
        return {"plan_end_reason": "completed"}

    def execute_plan_step(state: ChatState, config: RunnableConfig):
        step = state.remaining_steps[0]
        payload = {
            "resolved_request": state.resolved_request,
            "current_step": step.model_dump(),
            "completed_step_records": [
                record.model_dump() for record in state.step_history
            ],
            "memory_context": state.memory_context,
            "plan_user_context": state.plan_user_context,
        }
        used_ask_user = False
        try:
            result = plan_step_agent.invoke(
                {
                    "messages": [
                        SystemMessage(content=PLAN_STEP_PROMPT),
                        HumanMessage(
                            content=json.dumps(payload, ensure_ascii=False)
                        ),
                    ]
                },
                config=config,
            )
            submission = _structured_result(
                result["structured_response"],
                StepResultSubmission,
            )
            used_ask_user = any(
                tool_call.get("name") == "ask_user"
                for message in result["messages"]
                if isinstance(message, AIMessage)
                for tool_call in message.tool_calls
            )
        except ToolCallLimitExceededError:
            submission = StepResultSubmission(
                status="failed",
                result="当前步骤达到工具调用上限，未能满足成功标准。",
            )
        except GraphBubbleUp:
            raise
        except Exception as exc:
            reason = str(exc).strip()
            detail = f": {reason[:300]}" if reason else ""
            submission = StepResultSubmission(
                status="failed",
                result=f"当前步骤执行失败（{type(exc).__name__}{detail}）。",
            )

        record = StepExecutionRecord(step=step, result=submission)
        return {
            "remaining_steps": state.remaining_steps[1:],
            "step_history": [*state.step_history, record],
            "step_used_ask_user": used_ask_user,
        }

    def route_after_plan_step(state: ChatState):
        latest = state.step_history[-1]
        if latest.result.status == "blocked":
            return "mark_plan_blocked"
        if latest.result.status == "failed":
            return "replan_or_finish"
        if state.step_used_ask_user:
            return "replan_or_finish"
        if latest.step.review_after:
            return "replan_or_finish"
        return "before_plan_step"

    def mark_plan_blocked(state: ChatState):
        return {"plan_end_reason": "blocked"}

    def replan_or_finish(
        state: ChatState,
    ) -> Command[Literal["before_plan_step", "summarize_plan_result"]]:
        result = llm.with_structured_output(ReplanDecision).invoke([
            SystemMessage(content=REPLAN_PROMPT),
            HumanMessage(content=json.dumps({
                "resolved_request": state.resolved_request,
                "step_history": [
                    record.model_dump() for record in state.step_history
                ],
                "remaining_steps": [
                    step.model_dump() for step in state.remaining_steps
                ],
                "plan_user_context": state.plan_user_context,
                "remaining_hard_budget": (
                    settings.plan_hard_execution_limit
                    - len(state.step_history)
                ),
            }, ensure_ascii=False)),
        ])
        decision = _structured_result(result, ReplanDecision).root
        if decision.outcome == "continue":
            return Command(
                update={"step_used_ask_user": False},
                goto="before_plan_step",
            )
        if decision.outcome == "revise":
            return Command(
                update={
                    "remaining_steps": decision.remaining_steps,
                    "step_used_ask_user": False,
                },
                goto="before_plan_step",
            )
        return Command(
            update={
                "plan_end_reason": "planner_finished",
                "step_used_ask_user": False,
            },
            goto="summarize_plan_result",
        )

    def review_plan_budget(
        state: ChatState,
    ) -> Command[Literal[
        "before_plan_step",
        "replan_or_finish",
        "summarize_plan_result",
    ]]:
        response = interrupt({
            "type": "plan_budget",
            "completed_steps": len(state.step_history),
            "remaining_steps": [
                step.instruction for step in state.remaining_steps
            ],
            "hard_limit": settings.plan_hard_execution_limit,
            "options": ["continue", "continue_with_input", "stop"],
        })
        decision = response.get("decision")
        if decision == "continue":
            return Command(
                update={
                    "authorized_step_limit": settings.plan_hard_execution_limit,
                },
                goto="before_plan_step",
            )
        if decision == "continue_with_input":
            message = str(response.get("message", "")).strip()
            if not message:
                raise ValueError("continue_with_input requires a message")
            return Command(
                update={
                    "authorized_step_limit": settings.plan_hard_execution_limit,
                    "plan_user_context": [*state.plan_user_context, message],
                },
                goto="replan_or_finish",
            )
        if decision == "stop":
            return Command(
                update={"plan_end_reason": "user_stopped"},
                goto="summarize_plan_result",
            )
        raise ValueError("unknown plan budget decision")

    def summarize_plan_result(state: ChatState):
        response = llm.invoke([
            SystemMessage(content=SUMMARY_PLAN_PROMPT),
            HumanMessage(content=json.dumps({
                "resolved_request": state.resolved_request,
                "step_history": [
                    record.model_dump() for record in state.step_history
                ],
                "remaining_steps": [
                    step.model_dump() for step in state.remaining_steps
                ],
                "plan_user_context": state.plan_user_context,
                "end_reason": state.plan_end_reason,
            }, ensure_ascii=False)),
        ])
        return {"assistant_text": _message_text(response)}

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

    def route_execution_mode(state: ChatState):
        if state.execution_mode == "plan_execute":
            return "create_plan"
        return "run_react_agent"

    workflow = StateGraph(ChatState)
    workflow.add_node("initialize_turn", initialize_turn)
    workflow.add_node("understand_request", understand_request)
    workflow.add_node("build_memory_context", build_memory_context)
    workflow.add_node("reply_clarification", reply_clarification)
    workflow.add_node("run_react_agent", run_react_agent)
    workflow.add_node("create_plan", create_plan)
    workflow.add_node("before_plan_step", before_plan_step)
    workflow.add_node("mark_plan_hard_limit", mark_plan_hard_limit)
    workflow.add_node("mark_plan_completed", mark_plan_completed)
    workflow.add_node("execute_plan_step", execute_plan_step)
    workflow.add_node("mark_plan_blocked", mark_plan_blocked)
    workflow.add_node("replan_or_finish", replan_or_finish)
    workflow.add_node("review_plan_budget", review_plan_budget)
    workflow.add_node("summarize_plan_result", summarize_plan_result)
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
    workflow.add_conditional_edges(
        "build_memory_context",
        route_execution_mode,
        {
            "run_react_agent": "run_react_agent",
            "create_plan": "create_plan",
        },
    )
    workflow.add_edge("reply_clarification", "finalize_reply")
    workflow.add_edge("run_react_agent", "finalize_reply")
    workflow.add_edge("create_plan", "before_plan_step")
    workflow.add_edge("mark_plan_hard_limit", "summarize_plan_result")
    workflow.add_edge("mark_plan_completed", "summarize_plan_result")
    workflow.add_conditional_edges(
        "execute_plan_step",
        route_after_plan_step,
        {
            "mark_plan_blocked": "mark_plan_blocked",
            "replan_or_finish": "replan_or_finish",
            "before_plan_step": "before_plan_step",
        },
    )
    workflow.add_edge("mark_plan_blocked", "summarize_plan_result")
    workflow.add_edge("summarize_plan_result", "finalize_reply")
    workflow.add_edge("finalize_reply", "finish_turn")
    workflow.add_edge("finish_turn", END)
    return workflow.compile(checkpointer=checkpointer)
