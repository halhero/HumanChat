from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import END, START, StateGraph

from human_chat.character import load_character
from human_chat.config import PROJECT_ROOT, Settings, load_settings
from human_chat.logging_config import get_logger
from human_chat.llm import create_chat_model
from human_chat.memory_store import format_memory_for_prompt, load_memory
from human_chat.schemas import ChatState, ToolDecision, TtsResponse
from human_chat.tools import list_project_files, read_project_file, search_project_text
from human_chat.tts import TtsClient, TtsError


logger = get_logger(__name__)


STRUCTURED_OUTPUT_INSTRUCTION = """
请以 JSON 格式返回，格式为：
{"text": "你的回答"}
"""

TOOL_DECISION_PROMPT = """
你需要判断本轮用户问题是否需要读取当前项目文件才能可靠回答。

可用工具：
- list_project_files: 列出项目文件，不需要参数。
- read_project_file: 读取项目内文本文件，参数 path。
- search_project_text: 搜索项目文本，参数 query。

只有当问题明确需要项目代码、文件内容、当前实现细节时才使用工具。
如果不需要工具，need_tool=false。
"""


def _build_system_prompt(character, memory_prompt: str) -> str:
    return (
        f"{character.system_prompt}\n"
        f"以下是你应该长期记住的用户和项目背景：\n"
        f"{memory_prompt}\n"
        f"请使用角色配置指定的语言回复：{character.reply_language}。\n"
        f"{STRUCTURED_OUTPUT_INSTRUCTION}"
    )


def _format_tool_result_for_prompt(tool_result: str) -> str:
    if not tool_result:
        return "本轮没有调用工具。"
    return f"以下是本轮工具返回的项目上下文：\n{tool_result}"


def build_graph(settings: Settings | None = None):
    settings = settings or load_settings()
    character = load_character(settings.character_path)
    llm = create_chat_model(settings)
    tts_client = TtsClient(settings, character)

    def prepare_context(state: ChatState):
        memory = load_memory(settings.memory_path)
        return {"memory_prompt": format_memory_for_prompt(memory)}

    def decide_tool_use(state: ChatState):
        decision_llm = llm.with_structured_output(ToolDecision)
        decision = decision_llm.invoke(
            [
                HumanMessage(
                    content=(
                        f"{TOOL_DECISION_PROMPT}\n\n"
                        f"用户问题：\n{state.question}"
                    )
                )
            ]
        )
        return {"tool_request": decision.dict()}

    def execute_tool(state: ChatState):
        request = state.tool_request or {}
        tool_name = request.get("tool_name", "")
        arguments = request.get("arguments", {})

        try:
            if tool_name == "list_project_files":
                files = list_project_files(PROJECT_ROOT, limit=80)
                return {"tool_result": "\n".join(files)}

            if tool_name == "read_project_file":
                content = read_project_file(PROJECT_ROOT, arguments.get("path", ""))
                return {"tool_result": content}

            if tool_name == "search_project_text":
                matches = search_project_text(PROJECT_ROOT, arguments.get("query", ""), limit=40)
                lines = [f"{item['path']}:{item['line']}: {item['text']}" for item in matches]
                return {"tool_result": "\n".join(lines) if lines else "未找到匹配内容。"}

            return {"tool_result": f"未知工具：{tool_name}"}
        except Exception as exc:
            logger.warning("Tool execution failed: %s", exc)
            return {"tool_result": f"工具调用失败：{exc}"}

    def generate_reply(state: ChatState):
        prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", _build_system_prompt(character, state.memory_prompt)),
                ("system", _format_tool_result_for_prompt(state.tool_result)),
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
            "tts_text": response.text,
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

    def route_after_tool_decision(state: ChatState):
        if (state.tool_request or {}).get("need_tool"):
            return "execute_tool"
        return "generate_reply"

    workflow = StateGraph(ChatState)
    workflow.add_node("prepare_context", prepare_context)
    workflow.add_node("decide_tool_use", decide_tool_use)
    workflow.add_node("execute_tool", execute_tool)
    workflow.add_node("generate_reply", generate_reply)
    workflow.add_node("synthesize_speech", synthesize_speech)
    workflow.add_edge(START, "prepare_context")
    workflow.add_edge("prepare_context", "decide_tool_use")
    workflow.add_conditional_edges(
        "decide_tool_use",
        route_after_tool_decision,
        {
            "execute_tool": "execute_tool",
            "generate_reply": "generate_reply",
        },
    )
    workflow.add_edge("execute_tool", "generate_reply")
    workflow.add_edge("generate_reply", "synthesize_speech")
    workflow.add_edge("synthesize_speech", END)
    return workflow.compile()
