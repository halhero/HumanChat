from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import END, START, StateGraph

from human_chat.character import load_character
from human_chat.config import Settings, load_settings
from human_chat.logging_config import get_logger
from human_chat.llm import create_chat_model
from human_chat.schemas import ChatState, TtsResponse
from human_chat.tts import TtsClient, TtsError


logger = get_logger(__name__)


STRUCTURED_OUTPUT_INSTRUCTION = """
请以 JSON 格式返回，格式为：
{"text": "你的回答"}
"""


def _build_system_prompt(character) -> str:
    return (
        f"{character.system_prompt}\n"
        f"请使用角色配置指定的语言回复：{character.reply_language}。\n"
        f"{STRUCTURED_OUTPUT_INSTRUCTION}"
    )


def build_graph(settings: Settings | None = None):
    settings = settings or load_settings()
    character = load_character(settings.character_path)
    llm = create_chat_model(settings)
    tts_client = TtsClient(settings, character)

    def chat(state: ChatState):
        prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", _build_system_prompt(character)),
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
            "tts_text": response.text,
            "tts_error": "",
            "messages": [
                HumanMessage(content=state.question),
                AIMessage(content=response.text),
            ],
        }

    def generate_speech(state: ChatState):
        try:
            tts_client.synthesize_and_play(state.tts_text)
        except TtsError as exc:
            logger.warning("TTS failed: %s", exc)
            return {"tts_error": str(exc)}
        return {"tts_error": ""}

    workflow = StateGraph(ChatState)
    workflow.add_node("chat", chat)
    workflow.add_node("generate_speech", generate_speech)
    workflow.add_edge(START, "chat")
    workflow.add_edge("chat", "generate_speech")
    workflow.add_edge("generate_speech", END)
    return workflow.compile()
