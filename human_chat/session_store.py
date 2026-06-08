import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

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
        "messages": [],
    }


def session_path(settings: Settings, session_id: str) -> Path:
    return settings.session_dir / f"{session_id}.json"


def save_session(settings: Settings, session: dict) -> None:
    settings.session_dir.mkdir(parents=True, exist_ok=True)
    session["updated_at"] = datetime.now(TIMEZONE).isoformat()
    path = session_path(settings, session["id"])
    path.write_text(
        json.dumps(session, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_session(settings: Settings, session_id: str) -> dict:
    path = session_path(settings, session_id)
    return json.loads(path.read_text(encoding="utf-8"))


def messages_to_dicts(messages: list[BaseMessage]) -> list[dict]:
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


def dicts_to_messages(items: list[dict]) -> list[BaseMessage]:
    messages: list[BaseMessage] = []

    for item in items:
        role = item.get("role")
        content = item.get("content", "")

        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    return messages
