from human_chat.storage.base import MemoryStore, SessionStore
from human_chat.storage.json_store import JsonMemoryStore, JsonSessionStore
from human_chat.memory_repository import LangGraphMemoryRepository, JsonMemoryRepository, MemoryRepository


def create_session_store(settings) -> SessionStore:
    return JsonSessionStore(settings)


def create_memory_store(settings) -> MemoryStore:
    return JsonMemoryStore(settings)


__all__ = [
    "JsonMemoryStore",
    "LangGraphMemoryRepository",
    "JsonMemoryRepository",
    "JsonSessionStore",
    "MemoryStore",
    "MemoryRepository",
    "SessionStore",
    "create_memory_store",
    "create_session_store",
]
