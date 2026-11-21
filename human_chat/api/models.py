"""Public request and response models shared by HumanChat API routes."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HealthFeatures(BaseModel):
    checkpoint_backend: str
    checkpoint_persistent: bool
    memory_backend: str
    memory_persistent: bool
    mcp_enabled: bool
    registered_tool_count: int = Field(ge=0)
    stt_enabled: bool
    tts_enabled: bool


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
    model_config = ConfigDict(extra="forbid")
    decision: Literal[
        "approve",
        "reject",
        "respond",
        "continue",
        "continue_with_input",
        "stop",
    ]
    message: str | None = None

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_required_message(self):
        if self.decision in {"respond", "continue_with_input"} and not self.message:
            raise ValueError(f"{self.decision} requires a non-empty message")
        return self


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


class VoiceCapabilitiesResponse(BaseModel):
    stt_enabled: bool
    tts_enabled: bool
    tts_available: bool
    tts_auto_start: bool
    max_audio_bytes: int = Field(ge=1024)


class TranscriptionResponse(BaseModel):
    text: str


class SpeechSynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5_000)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text must not be blank")
        return normalized
