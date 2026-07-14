from typing import Protocol

from human_chat.memory_store import LongTermMemory


class SessionStore(Protocol):
    def create(self) -> dict:
        ...

    def load(self, session_id: str) -> dict:
        ...

    def save(self, session: dict) -> None:
        ...

    def list_recent(self, limit: int = 10) -> list[dict]:
        ...


class MemoryStore(Protocol):
    def load(self) -> LongTermMemory:
        ...

    def save(self, memory: LongTermMemory) -> None:
        ...

    def add(self, text: str, source: str = "manual", confidence: float | None = None) -> bool:
        ...

    def delete(self, index: int) -> str | None:
        ...

    def format_for_prompt(self) -> str:
        ...
