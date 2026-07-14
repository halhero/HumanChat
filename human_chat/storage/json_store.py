from human_chat.config import Settings
from human_chat.memory_repository import JsonMemoryRepository, default_memory_namespace
from human_chat.memory_store import (
    LongTermMemory,
    add_memory_item,
    delete_memory_item,
    format_memory_for_prompt,
)
from human_chat.session_store import create_session, list_sessions, load_session, save_session


class JsonSessionStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def create(self) -> dict:
        session = create_session()
        self.save(session)
        return session

    def load(self, session_id: str) -> dict:
        return load_session(self.settings, session_id)

    def save(self, session: dict) -> None:
        save_session(self.settings, session)

    def list_recent(self, limit: int = 10) -> list[dict]:
        return list_sessions(self.settings, limit=limit)


class JsonMemoryStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.namespace = default_memory_namespace(settings)
        self.repository = JsonMemoryRepository(settings.memory_path, self.namespace)

    def load(self) -> LongTermMemory:
        return self.repository.load_memory(self.namespace)

    def save(self, memory: LongTermMemory) -> None:
        self.repository.save_memory(self.namespace, memory)

    def add(self, text: str, source: str = "manual", confidence: float | None = None) -> bool:
        memory = self.load()
        added = add_memory_item(memory, text, source=source, confidence=confidence)
        if added:
            self.save(memory)
        return added

    def delete(self, index: int) -> str | None:
        memory = self.load()
        deleted = delete_memory_item(memory, index)
        if deleted is not None:
            self.save(memory)
        return deleted

    def format_for_prompt(self) -> str:
        return format_memory_for_prompt(self.load())
