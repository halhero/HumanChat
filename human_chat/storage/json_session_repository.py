import json
import re
from pathlib import Path
from uuid import uuid4

from human_chat.session_models import SessionRecord


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class JsonSessionRepository:
    def __init__(self, directory: Path):
        self.directory = directory

    def create(self) -> SessionRecord:
        session = SessionRecord.create()
        self.save(session)
        return session

    def load(self, session_id: str) -> SessionRecord:
        path = self._session_path(session_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("id", path.stem)
        return SessionRecord.from_dict(data)

    def save(self, session: SessionRecord) -> None:
        path = self._session_path(session.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary_path.write_text(
                json.dumps(_model_to_dict(session), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def list_recent(self, limit: int = 10) -> list[SessionRecord]:
        if limit <= 0 or not self.directory.exists():
            return []

        sessions = []
        for path in self.directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data.setdefault("id", path.stem)
                sessions.append(SessionRecord.from_dict(data))
            except (OSError, ValueError, json.JSONDecodeError):
                continue

        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions[:limit]

    def _session_path(self, session_id: str) -> Path:
        normalized = session_id.strip()
        if not normalized or not SESSION_ID_PATTERN.fullmatch(normalized):
            raise ValueError("会话 ID 格式无效。")
        return self.directory / f"{normalized}.json"


def _model_to_dict(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return json.loads(model.json())
