from human_chat.memory_repository import (
    JsonMemoryRepository,
    LangGraphMemoryRepository,
    MemoryRepository,
    default_memory_namespace,
)
from human_chat.memory_service import LongTermMemoryService, MemoryService
from human_chat.storage.base import SessionStore
from human_chat.storage.json_session_store import JsonSessionStore


def create_session_store(settings) -> SessionStore:
    return JsonSessionStore(settings)


def create_memory_service(settings) -> MemoryService:
    namespace = default_memory_namespace(settings)
    repository = JsonMemoryRepository(settings.memory_path, namespace)
    return LongTermMemoryService(repository, namespace)


__all__ = [
    "LangGraphMemoryRepository",
    "JsonMemoryRepository",
    "JsonSessionStore",
    "LongTermMemoryService",
    "MemoryService",
    "MemoryRepository",
    "SessionStore",
    "create_memory_service",
    "create_session_store",
]
