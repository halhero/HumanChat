"""Public request and response models shared by HumanChat API routes."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class HealthFeatures(BaseModel):
    checkpoint_backend: str
    checkpoint_persistent: bool
    memory_backend: str
    memory_persistent: bool
    mcp_enabled: bool
    registered_tool_count: int = Field(ge=0)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["HumanChat API"] = "HumanChat API"
    version: str
    features: HealthFeatures


class SessionSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = Field(ge=0)


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]


class ChatMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str


class SessionDetailResponse(SessionSummary):
    messages: list[ChatMessageResponse]


class StartTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message must not be blank")
        return normalized


class ReviewDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    selected_item_ids: list[str] = Field(default_factory=list, max_length=100)


class TurnStatusResponse(BaseModel):
    id: str
    session_id: str
    status: Literal[
        "running",
        "awaiting_review",
        "cancelling",
        "cancelled",
        "completed",
        "failed",
    ]
    review: dict | None = None


class CancelTurnResponse(BaseModel):
    id: str
    status: Literal["cancelling", "cancelled"]
