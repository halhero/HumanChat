from human_chat.memory_repository import (
    JsonMemoryRepository,
    LangGraphMemoryRepository,
    MemoryRepository,
    default_memory_namespace,
)
from human_chat.memory_service import LongTermMemoryService, MemoryService
from human_chat.session_repository import SessionRepository
from human_chat.storage.json_session_repository import JsonSessionRepository


def create_session_repository(settings) -> SessionRepository:
    return JsonSessionRepository(settings.session_dir)


def create_memory_service(settings) -> MemoryService:
    namespace = default_memory_namespace(settings)
    repository = JsonMemoryRepository(settings.memory_path, namespace)
    return LongTermMemoryService(repository, namespace)


__all__ = [
    "LangGraphMemoryRepository",
    "JsonMemoryRepository",
    "JsonSessionRepository",
    "LongTermMemoryService",
    "MemoryService",
    "MemoryRepository",
    "SessionRepository",
    "create_memory_service",
    "create_session_repository",
]
