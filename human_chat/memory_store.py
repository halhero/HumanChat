import json
from pathlib import Path

from pydantic import BaseModel, Field


class LongTermMemory(BaseModel):
    preferences: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


DEFAULT_MEMORY = LongTermMemory(
    preferences=[
        "用户偏好中文沟通和讲解。",
        "用户希望在实际修改代码前，先看到设计说明和示例代码。",
        "用户希望解释尽量适合新手理解。",
    ],
    facts=[
        "用户正在开发 HumanChat 项目。",
        "HumanChat 是一个聊天 Agent 项目。",
        "HumanChat 当前使用 LangGraph、DashScope 兼容 OpenAI API 和 GPT-SoVITS。",
    ],
)


def load_memory(path: Path) -> LongTermMemory:
    if not path.exists():
        save_memory(path, DEFAULT_MEMORY)
        return DEFAULT_MEMORY

    data = json.loads(path.read_text(encoding="utf-8"))
    return LongTermMemory(**data)


def save_memory(path: Path, memory: LongTermMemory) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_memory_to_dict(memory), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_memory_for_prompt(memory: LongTermMemory) -> str:
    sections = []

    if memory.preferences:
        sections.append("用户偏好：")
        sections.extend(f"- {item}" for item in memory.preferences)

    if memory.facts:
        sections.append("长期事实：")
        sections.extend(f"- {item}" for item in memory.facts)

    if memory.notes:
        sections.append("备注：")
        sections.extend(f"- {item}" for item in memory.notes)

    if not sections:
        return "暂无长期记忆。"

    return "\n".join(sections)


def _memory_to_dict(memory: LongTermMemory) -> dict:
    if hasattr(memory, "model_dump"):
        return memory.model_dump()
    return memory.dict()
