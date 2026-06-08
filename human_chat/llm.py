from langchain_openai import ChatOpenAI

from human_chat.config import Settings


def create_chat_model(settings: Settings) -> ChatOpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured. Please set it in .env.")

    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key,
        base_url=settings.llm_base_url,
    )

