import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from human_chat.config import Settings


TIMEZONE = ZoneInfo("Asia/Shanghai")


def create_session_id() -> str:
    return datetime.now(TIMEZONE).strftime("%Y%m%d_%H%M%S")


def create_session() -> dict:
    now = datetime.now(TIMEZONE).isoformat()
    return {
        "id": create_session_id(),
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
    }


def session_path(settings: Settings, session_id: str) -> Path:
    return settings.session_dir / f"{session_id}.json"


def save_session(settings: Settings, session: dict) -> None:
    settings.session_dir.mkdir(parents=True, exist_ok=True)
    session_to_save = dict(session)
    messages = session_to_save.pop("messages", [])
    session_to_save["message_count"] = session_to_save.get("message_count", len(messages))
    session_to_save["updated_at"] = datetime.now(TIMEZONE).isoformat()
    path = session_path(settings, session_to_save["id"])
    path.write_text(
        json.dumps(session_to_save, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    session.clear()
    session.update(session_to_save)


def load_session(settings: Settings, session_id: str) -> dict:
    path = session_path(settings, session_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_sessions(settings: Settings, limit: int = 10) -> list[dict]:
    if not settings.session_dir.exists():
        return []

    sessions = []

    for path in settings.session_dir.glob("*.json"):
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        sessions.append(
            {
                "id": session.get("id", path.stem),
                "created_at": session.get("created_at", ""),
                "updated_at": session.get("updated_at", ""),
                "message_count": session.get("message_count", len(session.get("messages", []))),
            }
        )

    sessions.sort(key=lambda item: item["updated_at"], reverse=True)
    return sessions[:limit]


def messages_to_dicts(messages: list[Any]) -> list[dict]:
    from langchain_core.messages import AIMessage, HumanMessage

    items = []

    for message in messages:
        if isinstance(message, HumanMessage):
            role = "user"
        elif isinstance(message, AIMessage):
            role = "assistant"
        else:
            role = message.type

        items.append(
            {
                "role": role,
                "content": message.content,
            }
        )

    return items


def dicts_to_messages(items: list[dict]) -> list[Any]:
    from langchain_core.messages import AIMessage, HumanMessage

    messages: list[Any] = []

    for item in items:
        role = item.get("role")
        content = item.get("content", "")

        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    return messages
