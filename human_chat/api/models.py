"""Public request and response schemas shared by HumanChat API routes."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class HealthFeatures(BaseModel):
    checkpoint_backend: str
    checkpoint_persistent: bool
    memory_backend: str
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
    recoverable: bool


class SessionListResponse(BaseModel):
    items: list[SessionSummary] = Field(default_factory=list)


class ChatMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str


class SessionDetailResponse(BaseModel):
    session: SessionSummary
    messages: list[ChatMessageResponse] = Field(default_factory=list)


class StartTurnRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("question must contain visible characters")
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
    review: dict[str, Any] | None = None


class CancelTurnResponse(BaseModel):
    id: str
    status: Literal["cancelling", "cancelled"]
