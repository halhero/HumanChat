from typing import Protocol


class SessionStore(Protocol):
    def create(self) -> dict:
        ...

    def load(self, session_id: str) -> dict:
        ...

    def save(self, session: dict) -> None:
        ...

    def list_recent(self, limit: int = 10) -> list[dict]:
        ...
