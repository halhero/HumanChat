"""Typed in-process models and expected errors for conversation coordination."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from langgraph.runtime import RunControl

from human_chat.runtime import ChatRuntime
from human_chat.session_models import now_local


STREAM_END = object()


class TurnStatus(StrEnum):
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class ConversationError(RuntimeError):
    """Base class for expected application-layer conversation errors."""


class SessionNotFoundError(ConversationError):
    pass


class SessionBusyError(ConversationError):
    pass


class TurnNotFoundError(ConversationError):
    pass


class TurnStateError(ConversationError):
    pass


class ReviewDecisionError(ConversationError):
    pass


@dataclass(frozen=True)
class ConversationMessage:
    id: str
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ConversationEvent:
    type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class PendingReview:
    kind: Literal["tool", "memory"]
    public_payload: dict[str, Any]
    memory_items: dict[str, str] = field(default_factory=dict)


@dataclass
class ConversationTurn:
    id: str
    session_id: str
    runtime: ChatRuntime
    status: TurnStatus = TurnStatus.RUNNING
    pending_review: PendingReview | None = None
    control: RunControl | None = None
    task: asyncio.Task | None = None
    updated_at: datetime = field(default_factory=now_local)


@dataclass(frozen=True)
class ConversationStream:
    turn_id: str
    queue: asyncio.Queue


@dataclass(frozen=True)
class PhaseOutcome:
    review: PendingReview | None = None
