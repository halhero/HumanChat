from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


TIMEZONE = ZoneInfo("Asia/Shanghai")


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


class SessionRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    thread_id: str = ""
    title: str = "新对话"
    created_at: datetime = Field(default_factory=now_local)
    updated_at: datetime = Field(default_factory=now_local)
    message_count: int = Field(default=0, ge=0)
    checkpoint_backend: str = "pending"
    recoverable: bool = False

    @classmethod
    def create(cls) -> "SessionRecord":
        session_id = uuid4().hex
        now = now_local()
        return cls(
            id=session_id,
            thread_id=session_id,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "SessionRecord":
        normalized = dict(data)
        session_id = str(normalized.get("id", "")).strip()
        normalized["id"] = session_id
        normalized.setdefault("thread_id", session_id)
        normalized.setdefault("checkpoint_backend", "legacy")
        normalized.setdefault("recoverable", True)
        return cls(**normalized)
