from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field


class MemoryCandidate(BaseModel):
    category: str = Field(description="One of: preference, fact, note.")
    text: str = Field(description="A concise long-term memory candidate.")


class MemoryExtractionResult(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)


MEMORY_EXTRACTION_PROMPT = """
你负责从一轮对话中提取值得长期记住的信息。

只提取稳定、长期、有助于未来协作的信息。不要提取临时情绪、普通寒暄、一次性任务细节。

分类规则：
- preference: 用户偏好，例如沟通方式、代码修改方式、输出风格。
- fact: 稳定事实，例如项目目标、技术栈、用户长期正在做的事情。
- note: 其他有长期参考价值的信息。

如果没有值得记住的信息，返回空 candidates。
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
