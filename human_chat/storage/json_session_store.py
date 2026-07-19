from human_chat.config import Settings
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
