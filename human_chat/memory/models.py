"""Shared contracts for canonical long-term memory records."""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Assertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, description="认识的主体，例如用户或明确项目名，而不是整句目标")
    relation: str = Field(min_length=1, description="主体与内容的关系，例如目标、使用系统、回答方式")
    scope: str = ""
    value: str | None = Field(default=None, description="完整认识的核心内容；仅局部维护时可空，新增认识必须非空")
    negated: bool = False
    state: str | None = None
    deadline: str | None = None
    effective_time: str | None = None
    expires_at: AwareDatetime | None = None

    def identity(self):
        return tuple(s.strip().casefold() for s in (self.subject, self.relation, self.scope))

    def render(self):
        parts = [self.subject, self.relation, self.value or ""]
        if self.negated:
            parts.insert(1, "不")
        for label, value in (("范围", self.scope), ("状态", self.state),
                             ("期限", self.deadline), ("事实时间", self.effective_time)):
            if value:
                parts.append(f"{label}：{value}")
        return "；".join(part for part in parts if part)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    message_id: str
    user_id: str
    session_id: str
    message_time: AwareDatetime
    quote: str = Field(min_length=1)


class MemoryVersion(BaseModel):
    version: int
    text: str
    assertion: Assertion | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    observed_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    status: Literal["historical", "revoked"]


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    text: str
    created_at: str | None = Field(default_factory=now_iso)
    updated_at: str | None = Field(default_factory=now_iso)
    source: str = "manual"
    confidence: float | None = None
    user_id: str | None = None
    assertion: Assertion | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    context_message_ids: list[str] = Field(default_factory=list)
    version: int = 1
    status: Literal["active", "retracted", "deleted"] = "active"
    history: list[MemoryVersion] = Field(default_factory=list)
    observed_at: AwareDatetime | None = None
    last_operation_id: str | None = None

    def usable_now(self, now=None):
        now = now or datetime.now(timezone.utc)
        return self.status == "active" and not (
            self.assertion and self.assertion.expires_at and self.assertion.expires_at <= now
        )


PatchField = Literal["value", "negated", "state", "deadline", "effective_time", "expires_at", "scope"]


class Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    user_id: str
    kind: Literal["add", "duplicate", "update", "correct", "retract", "delete"]
    text: str
    assertion: Assertion | None = None
    evidence: list[Evidence] = Field(min_length=1)
    context_message_ids: list[str] = Field(default_factory=list)
    target_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1, strict=True)
    fields: list[PatchField] = Field(default_factory=list)
    revoke_history: bool = False
    reason: str = ""

class MutationResult(BaseModel):
    operation_id: str
    outcome: Literal["applied", "already_applied", "conflict", "rejected"]
    item_id: str | None = None
    reason: str = ""
