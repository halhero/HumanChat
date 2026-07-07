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
    category: str
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
                        category=category,
                        text=text,
                        source="legacy",
                    )
                )
        if legacy_items:
            data["items"] = [*data.get("items", []), *legacy_items]
        super().__init__(**data)

    @property
    def preferences(self) -> list[str]:
        return get_memory_items(self, "preferences")

    @property
    def facts(self) -> list[str]:
        return get_memory_items(self, "facts")

    @property
    def notes(self) -> list[str]:
        return get_memory_items(self, "notes")


DEFAULT_MEMORY = LongTermMemory(
    items=[
        MemoryItem(category="preferences", text="用户偏好中文沟通和讲解。", source="default"),
        MemoryItem(
            category="preferences",
            text="用户希望在实际修改代码前，先看到设计说明和示例代码。",
            source="default",
        ),
        MemoryItem(category="preferences", text="用户希望解释尽量适合新手理解。", source="default"),
        MemoryItem(category="facts", text="用户正在开发 HumanChat 项目。", source="default"),
        MemoryItem(category="facts", text="HumanChat 是一个聊天 Agent 项目。", source="default"),
        MemoryItem(
            category="facts",
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


def add_memory_item(memory: LongTermMemory, category: str, text: str) -> bool:
    normalized_category = _normalize_category(category)
    normalized = text.strip()
    items = get_memory_items(memory, normalized_category)
    if not normalized or normalized in items:
        return False
    memory.items.append(
        MemoryItem(
            category=normalized_category,
            text=normalized,
            source="manual",
        )
    )
    return True


def delete_memory_item(memory: LongTermMemory, category: str, index: int) -> str | None:
    items = _get_category_memory_items(memory, category)
    zero_based_index = index - 1
    if zero_based_index < 0 or zero_based_index >= len(items):
        return None
    item = items[zero_based_index]
    memory.items = [existing for existing in memory.items if existing.id != item.id]
    return item.text


def get_memory_items(memory: LongTermMemory, category: str) -> list[str]:
    return [item.text for item in _get_category_memory_items(memory, category)]


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


def _get_category_memory_items(memory: LongTermMemory, category: str) -> list[MemoryItem]:
    normalized = _normalize_category(category)
    return [item for item in memory.items if item.category == normalized]


def _normalize_category(category: str) -> str:
    normalized = category.strip().lower()
    aliases = {
        "preference": "preferences",
        "preferences": "preferences",
        "pref": "preferences",
        "fact": "facts",
        "facts": "facts",
        "note": "notes",
        "notes": "notes",
    }
    if normalized not in aliases:
        raise ValueError(f"Unsupported memory category: {category}")
    return aliases[normalized]
