from langchain_core.messages import HumanMessage

from human_chat.memory_review import MemoryCandidate, MemoryExtractionResult


MEMORY_EXTRACTION_PROMPT = """
你负责从一轮对话中提取值得长期记住的信息。

只提取稳定、长期、有助于未来协作的信息。不要提取临时情绪、普通寒暄、一次性任务细节。

如果没有值得记住的信息，返回空 candidates。

请只返回候选记忆文本，不要给候选记忆分类。
"""


def extract_memory_candidates(llm, user_text: str, assistant_text: str) -> list[MemoryCandidate]:
    structured_llm = llm.with_structured_output(MemoryExtractionResult)
    result = structured_llm.invoke(
        [
            HumanMessage(
                content=(
                    f"{MEMORY_EXTRACTION_PROMPT}\n\n"
                    f"用户消息：\n{user_text}\n\n"
                    f"助手回复：\n{assistant_text}"
                )
            )
        ]
    )
    return [candidate for candidate in result.candidates if candidate.text.strip()]
