"""Canonical memory persistence with bounded, ID-directed reads."""

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Protocol

from human_chat.config import Settings
from human_chat.memory.models import MemoryItem

MemoryNamespace = tuple[str, ...]


def default_memory_namespace(settings: Settings) -> MemoryNamespace:
    return ("users", settings.memory_user_id, "memory")


def namespace_key(namespace: MemoryNamespace) -> str:
    return json.dumps(namespace, ensure_ascii=False, separators=(",", ":"))


class MemoryRepository(Protocol):
    def get_item(self, namespace: MemoryNamespace, item_id: str) -> MemoryItem | None:
        ...

    def get_items(self, namespace: MemoryNamespace, item_ids: list[str]) -> list[MemoryItem]:
        ...

    def upsert_item(self, namespace: MemoryNamespace, item: MemoryItem) -> None:
        ...

    def delete_item(self, namespace: MemoryNamespace, item_id: str) -> bool:
        ...


class SqliteMemoryRepository:
    """Local canonical store; one row is one complete MemoryItem."""

    def __init__(self, path: Path | None):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = RLock()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS memory_items (
            namespace TEXT NOT NULL,
            item_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (namespace, item_id)
        )""")
        self._db.commit()

    def get_item(self, namespace: MemoryNamespace, item_id: str) -> MemoryItem | None:
        with self._lock:
            row = self._db.execute(
                "SELECT payload FROM memory_items WHERE namespace=? AND item_id=?",
                (namespace_key(namespace), item_id),
            ).fetchone()
        return MemoryItem.model_validate_json(row["payload"]) if row else None

    def get_items(self, namespace: MemoryNamespace, item_ids: list[str]) -> list[MemoryItem]:
        ordered_ids = list(dict.fromkeys(item_ids))
        if not ordered_ids:
            return []
        placeholders = ",".join("?" for _ in ordered_ids)
        with self._lock:
            rows = self._db.execute(
                f"SELECT item_id,payload FROM memory_items WHERE namespace=? AND item_id IN ({placeholders})",
                (namespace_key(namespace), *ordered_ids),
            ).fetchall()
        by_id = {row["item_id"]: MemoryItem.model_validate_json(row["payload"]) for row in rows}
        return [by_id[item_id] for item_id in ordered_ids if item_id in by_id]

    def upsert_item(self, namespace: MemoryNamespace, item: MemoryItem) -> None:
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO memory_items(namespace,item_id,version,status,payload)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(namespace,item_id) DO UPDATE SET
                     version=excluded.version,status=excluded.status,payload=excluded.payload""",
                (namespace_key(namespace), item.id, item.version, item.status, item.model_dump_json()),
            )

    def delete_item(self, namespace: MemoryNamespace, item_id: str) -> bool:
        with self._lock, self._db:
            cursor = self._db.execute(
                "DELETE FROM memory_items WHERE namespace=? AND item_id=?",
                (namespace_key(namespace), item_id),
            )
        return cursor.rowcount > 0

    def close(self) -> None:
        self._db.close()


class LangGraphMemoryRepository:
    """Canonical adapter for LangGraph stores; reads stay bounded by supplied IDs."""

    def __init__(self, store):
        self.store = store

    def get_item(self, namespace: MemoryNamespace, item_id: str) -> MemoryItem | None:
        stored = self.store.get(namespace, item_id)
        return _memory_item_from_store(stored) if stored else None

    def get_items(self, namespace: MemoryNamespace, item_ids: list[str]) -> list[MemoryItem]:
        items = [self.get_item(namespace, item_id) for item_id in dict.fromkeys(item_ids)]
        return [item for item in items if item is not None]

    def upsert_item(self, namespace: MemoryNamespace, item: MemoryItem) -> None:
        self.store.put(namespace, item.id, item.model_dump(mode="json"))

    def delete_item(self, namespace: MemoryNamespace, item_id: str) -> bool:
        if self.store.get(namespace, item_id) is None:
            return False
        self.store.delete(namespace, item_id)
        return True


def _memory_item_from_store(stored) -> MemoryItem:
    value = dict(stored.value)
    value.setdefault("id", stored.key)
    return MemoryItem(**value)
