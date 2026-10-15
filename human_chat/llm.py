from langchain_openai import ChatOpenAI

from human_chat.config import Settings


def create_chat_model(settings: Settings, *, for_memory: bool = False) -> ChatOpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured. Please set it in .env.")

    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
        max_tokens=settings.memory_output_tokens if for_memory else settings.chat_output_tokens,
        # Current Qwen memory calls need usable JSON, not an output window spent on reasoning.
        extra_body={"enable_thinking": False} if for_memory and settings.llm_model.startswith("qwen") else None,
    )

