from human_chat.config import Settings
from human_chat.memory_repository import JsonMemoryRepository, default_memory_namespace
from human_chat.memory_store import (
    LongTermMemory,
    format_memory_for_prompt,
    MemoryItem,
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
        normalized = text.strip()
        if not normalized:
            return False
        if normalized in [item.text for item in self.repository.list_items(self.namespace)]:
            return False
        self.repository.put_item(
            self.namespace,
            MemoryItem(text=normalized, source=source, confidence=confidence),
        )
        return True

    def delete(self, index: int) -> str | None:
        items = self.repository.list_items(self.namespace)
        zero_based_index = index - 1
        if zero_based_index < 0 or zero_based_index >= len(items):
            return None
        item = items[zero_based_index]
        if not self.repository.delete_item(self.namespace, item.id):
            return None
        return item.text

    def format_for_prompt(self) -> str:
        return format_memory_for_prompt(self.load())
