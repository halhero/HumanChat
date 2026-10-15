"""Message provenance shared by conversation and memory processing."""

from datetime import datetime, timezone
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class SourceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    user_id: str
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    occurred_at: AwareDatetime | None = None

    def to_message(self):
        kind = HumanMessage if self.role == "user" else AIMessage
        return kind(id=self.id, content=self.content, additional_kwargs={
            "human_chat_source": self.model_dump(mode="json", exclude={"content"}),
        })


class SourceBatch(BaseModel):
    user_id: str
    new_messages: list[SourceMessage]
    context_messages: list[SourceMessage] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sources(self):
        sources = [*self.new_messages, *self.context_messages]
        if len({m.id for m in sources}) != len(sources):
            raise ValueError("Evidence and context must have distinct message ids")
        if any(m.user_id != self.user_id for m in sources):
            raise ValueError("Source belongs to another user")
        if any(m.role != "user" or m.occurred_at is None for m in self.new_messages):
            raise ValueError("New evidence requires timestamped user messages")
        return self


def new_source(content: str, *, user_id: str, session_id: str, role="user") -> SourceMessage:
    return SourceMessage(id=uuid4().hex, user_id=user_id, session_id=session_id,
                         role=role, content=content, occurred_at=datetime.now(timezone.utc))


def history_sources(messages, *, user_id: str, session_id: str):
    sources = []
    for index, message in enumerate(messages):
        if not isinstance(message, (HumanMessage, AIMessage)) or not isinstance(message.content, str):
            continue
        role = "user" if isinstance(message, HumanMessage) else "assistant"
        metadata = message.additional_kwargs.get("human_chat_source")
        if metadata:
            source = SourceMessage.model_validate({**metadata, "content": message.content})
            if (source.user_id != user_id or source.session_id != session_id
                    or source.id != message.id or source.role != role):
                raise ValueError("History source identity does not match its scope")
        else:
            source = SourceMessage(
                id=message.id or uuid5(NAMESPACE_URL, f"{session_id}:{index}:{message.content}").hex,
                user_id=user_id, session_id=session_id, role=role, content=message.content,
            )
        sources.append(source)
    return sources
