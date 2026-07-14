import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


TIMEZONE = ZoneInfo("Asia/Shanghai")


def _now_iso() -> str:
    return datetime.now(TIMEZONE).isoformat()


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    text: str
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    source: str = "manual"
    confidence: float | None = None


class LongTermMemory(BaseModel):
    items: list[MemoryItem] = Field(default_factory=list)

    def __init__(self, **data):
        legacy_items = []
        for category in ("preferences", "facts", "notes"):
            for text in data.pop(category, []) or []:
                legacy_items.append(
                    MemoryItem(
                        text=text,
                        source="legacy",
                    )
                )
        if legacy_items:
            data["items"] = [*data.get("items", []), *legacy_items]
        super().__init__(**data)


DEFAULT_MEMORY = LongTermMemory(
    items=[
        MemoryItem(text="用户偏好中文沟通和讲解。", source="default"),
        MemoryItem(
            text="用户希望在实际修改代码前，先看到设计说明和示例代码。",
            source="default",
        ),
        MemoryItem(text="用户希望解释尽量适合新手理解。", source="default"),
        MemoryItem(text="用户正在开发 HumanChat 项目。", source="default"),
        MemoryItem(text="HumanChat 是一个聊天 Agent 项目。", source="default"),
        MemoryItem(
            text="HumanChat 当前使用 LangGraph、DashScope 兼容 OpenAI API 和 GPT-SoVITS。",
            source="default",
        ),
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


def add_memory_item(
    memory: LongTermMemory,
    text: str,
    source: str = "manual",
    confidence: float | None = None,
) -> bool:
    normalized = text.strip()
    items = get_memory_items(memory)
    if not normalized or normalized in items:
        return False
    memory.items.append(
        MemoryItem(
            text=normalized,
            source=source,
            confidence=confidence,
        )
    )
    return True


def delete_memory_item(memory: LongTermMemory, index: int) -> str | None:
    items = list(memory.items)
    zero_based_index = index - 1
    if zero_based_index < 0 or zero_based_index >= len(items):
        return None
    item = items[zero_based_index]
    memory.items = [existing for existing in memory.items if existing.id != item.id]
    return item.text


def get_memory_items(memory: LongTermMemory) -> list[str]:
    return [item.text for item in memory.items]


def format_memory_for_prompt(memory: LongTermMemory) -> str:
    items = get_memory_items(memory)
    if not items:
        return "暂无长期记忆。"
    return "\n".join(["长期记忆：", *[f"- {item}" for item in items]])


def _memory_to_dict(memory: LongTermMemory) -> dict:
    if hasattr(memory, "model_dump"):
        return memory.model_dump()
    return memory.dict()

