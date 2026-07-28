from typing import Protocol

from human_chat.session_models import SessionRecord


class SessionRepository(Protocol):
    def create(self) -> SessionRecord:
        ...

    def load(self, session_id: str) -> SessionRecord:
        ...

    def save(self, session: SessionRecord) -> None:
        ...

    def list_recent(self, limit: int = 10) -> list[SessionRecord]:
        ...
