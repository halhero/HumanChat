from human_chat.storage.base import MemoryStore, SessionStore
from human_chat.storage.json_store import JsonMemoryStore, JsonSessionStore


def create_session_store(settings) -> SessionStore:
    return JsonSessionStore(settings)


def create_memory_store(settings) -> MemoryStore:
    return JsonMemoryStore(settings)


__all__ = [
    "JsonMemoryStore",
    "JsonSessionStore",
    "MemoryStore",
    "SessionStore",
    "create_memory_store",
    "create_session_store",
]
