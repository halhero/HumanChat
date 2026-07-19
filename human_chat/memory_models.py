from datetime import datetime
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
                legacy_items.append(MemoryItem(text=text, source="legacy"))
        if legacy_items:
            data["items"] = [*data.get("items", []), *legacy_items]
        super().__init__(**data)


def create_default_memory() -> LongTermMemory:
    return LongTermMemory(
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
        ]
    )
