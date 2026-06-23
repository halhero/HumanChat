from human_chat.config import Settings
from human_chat.memory_store import (
    LongTermMemory,
    add_memory_item,
    delete_memory_item,
    format_memory_for_prompt,
    load_memory,
    save_memory,
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

    def load(self) -> LongTermMemory:
        return load_memory(self.settings.memory_path)

    def save(self, memory: LongTermMemory) -> None:
        save_memory(self.settings.memory_path, memory)

    def add(self, category: str, text: str) -> bool:
        memory = self.load()
        added = add_memory_item(memory, category, text)
        if added:
            self.save(memory)
        return added

    def delete(self, category: str, index: int) -> str | None:
        memory = self.load()
        deleted = delete_memory_item(memory, category, index)
        if deleted is not None:
            self.save(memory)
        return deleted

    def format_for_prompt(self) -> str:
        return format_memory_for_prompt(self.load())
