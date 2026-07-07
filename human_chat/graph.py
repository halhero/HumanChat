from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from human_chat.character import load_character
from human_chat.config import Settings, load_settings
from human_chat.logging_config import get_logger
from human_chat.llm import create_chat_model
from human_chat.memory_extractor import extract_memory_candidates
from human_chat.schemas import ChatState, TtsResponse
from human_chat.storage import create_memory_store
from human_chat.tool_provider import create_tool_provider
from human_chat.tts import TtsClient, TtsError


logger = get_logger(__name__)


STRUCTURED_OUTPUT_INSTRUCTION = """
请以 JSON 格式返回，格式为：
{"text": "你的回答"}
"""

TOOL_CALLING_PROMPT = """
你可以使用项目只读工具获取 HumanChat 当前代码上下文。
只有当用户问题需要查看项目文件、搜索代码或理解当前实现时才调用工具。
如果工具结果仍不足以回答，可以继续调用工具。
如果不需要工具，或者已经收集到足够信息，请直接给出简短结论，不要调用工具。
"""

MAX_TOOL_CALL_ROUNDS = 3


def _build_system_prompt(character, memory_prompt: str) -> str:
    return (
        f"{character.system_prompt}\n"
        f"以下是你应该长期记住的用户和项目背景：\n"
        f"{memory_prompt}\n"
        f"请使用角色配置指定的语言回复：{character.reply_language}。\n"
        f"{STRUCTURED_OUTPUT_INSTRUCTION}"
    )


def _format_tool_messages_for_prompt(tool_messages: list) -> str:
    results = [
        str(message.content)
        for message in tool_messages
        if getattr(message, "type", "") == "tool"
    ]
    if not results:
        return "本轮没有调用工具。"
    return "以下是本轮工具返回的项目上下文：\n" + "\n\n".join(results)


def _format_tool_limit_notice(state: ChatState) -> str:
    if not state.tool_limit_reached:
        return ""
    return "\n\n注意：本轮工具调用已达到上限，请基于已经获得的工具结果回答。"


def _build_tool_user_prompt(question: str) -> str:
    return f"{TOOL_CALLING_PROMPT}\n\n用户问题：\n{question}"


def _latest_tool_conversation(tool_messages: list, question: str) -> list:
    user_prompt = _build_tool_user_prompt(question)
    for index in range(len(tool_messages) - 1, -1, -1):
        message = tool_messages[index]
        if isinstance(message, HumanMessage) and message.content == user_prompt:
            return tool_messages[index:]
    return []


def _tool_call_name(tool_call) -> str:
    if isinstance(tool_call, dict):
        return tool_call.get("name", "unknown_tool")
    return getattr(tool_call, "name", "unknown_tool")


def _tool_call_args(tool_call):
    if isinstance(tool_call, dict):
        return tool_call.get("args", {})
    return getattr(tool_call, "args", {})


def _build_tool_events(state: ChatState, tool_result_messages: list) -> list[dict]:
    if not state.tool_messages:
        return []
    last_message = state.tool_messages[-1]
    tool_calls = getattr(last_message, "tool_calls", []) or []
    events = []
    for index, tool_call in enumerate(tool_calls):
        result_message = tool_result_messages[index] if index < len(tool_result_messages) else None
        content = str(getattr(result_message, "content", ""))
        events.append(
            {
                "round": state.tool_call_count + 1,
                "tool": _tool_call_name(tool_call),
                "arguments": _tool_call_args(tool_call),
                "status": "error" if content.startswith("[tool_error]") else "success",
                "result_preview": content[:300],
            }
        )
    return events


def build_graph(settings: Settings | None = None, checkpointer=None):
    settings = settings or load_settings()
    character = load_character(settings.character_path)
    memory_store = create_memory_store(settings)
    llm = create_chat_model(settings)
    tool_provider = create_tool_provider(settings)
    project_tools = tool_provider.get_tools()
    tool_llm = llm.bind_tools(project_tools)
    tool_node = ToolNode(project_tools, messages_key="tool_messages")
    tts_client = TtsClient(settings, character)

    def prepare_context(state: ChatState):
        return {
            "memory_prompt": memory_store.format_for_prompt(),
            "tool_messages": [],
            "tool_call_count": 0,
            "tool_events": [],
            "tool_limit_reached": False,
            "memory_candidates": [],
        }

    def call_agent_model(state: ChatState):
        conversation = _latest_tool_conversation(state.tool_messages, state.question)
        if not conversation:
            human_message = HumanMessage(content=_build_tool_user_prompt(state.question))
            conversation = [human_message]

        response = tool_llm.invoke(conversation)
        return {"tool_messages": [*conversation, response]}

    def execute_project_tools(state: ChatState):
        result = tool_node.invoke({"tool_messages": state.tool_messages})
        tool_result_messages = result.get("tool_messages", [])
        return {
            "tool_messages": [*state.tool_messages, *tool_result_messages],
            "tool_call_count": state.tool_call_count + 1,
            "tool_events": [*state.tool_events, *_build_tool_events(state, tool_result_messages)],
        }

    def generate_reply(state: ChatState):
        current_tool_messages = _latest_tool_conversation(state.tool_messages, state.question)
        tool_context = (
            _format_tool_messages_for_prompt(current_tool_messages)
            + _format_tool_limit_notice(state)
        )
        prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", _build_system_prompt(character, state.memory_prompt)),
                ("system", tool_context),
                MessagesPlaceholder("messages"),
                ("human", "{question}"),
            ]
        )
        chain = prompt_template | llm.with_structured_output(TtsResponse)
        response = chain.invoke(
            {
                "question": state.question,
                "messages": state.messages,
            }
        )
        logger.info("Generated assistant reply")
        return {
            "assistant_text": response.text,
            "tts_error": "",
            "messages": [
                HumanMessage(content=state.question),
                AIMessage(content=response.text),
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
            return {"memory_candidates": []}

        try:
            candidates = extract_memory_candidates(llm, state.question, state.assistant_text)
        except Exception:
            logger.exception("Failed to extract memory candidates")
            return {"memory_candidates": []}

        return {"memory_candidates": [_model_to_dict(candidate) for candidate in candidates]}

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
            return "generate_reply"
        last_message = state.tool_messages[-1]
        if getattr(last_message, "tool_calls", None):
            if state.tool_call_count >= MAX_TOOL_CALL_ROUNDS:
                return "mark_tool_limit_reached"
            return "execute_project_tools"
        return "generate_reply"

    workflow = StateGraph(ChatState)
    workflow.add_node("prepare_context", prepare_context)
    workflow.add_node("call_agent_model", call_agent_model)
    workflow.add_node("execute_project_tools", execute_project_tools)
    workflow.add_node("generate_reply", generate_reply)
    workflow.add_node("extract_memory", extract_memory)
    workflow.add_node("synthesize_speech", synthesize_speech)
    workflow.add_node("mark_tool_limit_reached", mark_tool_limit_reached)
    workflow.add_edge(START, "prepare_context")
    workflow.add_edge("prepare_context", "call_agent_model")
    workflow.add_conditional_edges(
        "call_agent_model",
        route_after_agent_model,
        {
            "execute_project_tools": "execute_project_tools",
            "mark_tool_limit_reached": "mark_tool_limit_reached",
            "generate_reply": "generate_reply",
        },
    )
    workflow.add_edge("execute_project_tools", "call_agent_model")
    workflow.add_edge("mark_tool_limit_reached", "generate_reply")
    workflow.add_edge("generate_reply", "extract_memory")
    workflow.add_edge("extract_memory", "synthesize_speech")
    workflow.add_edge("synthesize_speech", END)
    if checkpointer is not None:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()


def _model_to_dict(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
