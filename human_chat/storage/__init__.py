from human_chat.session_repository import SessionRepository
from human_chat.storage.json_session_repository import JsonSessionRepository


def create_session_repository(settings) -> SessionRepository:
    return JsonSessionRepository(settings.session_dir)


__all__ = [
    "JsonSessionRepository",
    "SessionRepository",
    "create_session_repository",
]
