import json
import re
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from human_chat.config import Settings
from human_chat.memory_models import LongTermMemory, MemoryItem, create_default_memory


MemoryNamespace = tuple[str, ...]


def default_memory_namespace(settings: Settings) -> MemoryNamespace:
    return ("users", settings.memory_user_id, "memory")


class MemoryRepository(Protocol):
    def list_items(self, namespace: MemoryNamespace) -> list[MemoryItem]:
        ...

    def get_item(
        self,
        namespace: MemoryNamespace,
        item_id: str,
    ) -> MemoryItem | None:
        ...

    def upsert_item(self, namespace: MemoryNamespace, item: MemoryItem) -> None:
        ...

    def delete_item(self, namespace: MemoryNamespace, item_id: str) -> bool:
        ...


class JsonMemoryRepository:
    def __init__(self, base_path: Path):
        self.base_path = base_path

    def list_items(self, namespace: MemoryNamespace) -> list[MemoryItem]:
        return list(self._load_memory(namespace).items)

    def get_item(
        self,
        namespace: MemoryNamespace,
        item_id: str,
    ) -> MemoryItem | None:
        return next(
            (
                item
                for item in self._load_memory(namespace).items
                if item.id == item_id
            ),
            None,
        )

    def upsert_item(self, namespace: MemoryNamespace, item: MemoryItem) -> None:
        memory = self._load_memory(namespace)
        memory.items = [existing for existing in memory.items if existing.id != item.id]
        memory.items.append(item)
        self._save_memory(namespace, memory)

    def delete_item(self, namespace: MemoryNamespace, item_id: str) -> bool:
        memory = self._load_memory(namespace)
        original_count = len(memory.items)
        memory.items = [item for item in memory.items if item.id != item_id]
        if len(memory.items) == original_count:
            return False
        self._save_memory(namespace, memory)
        return True

    def _load_memory(self, namespace: MemoryNamespace) -> LongTermMemory:
        path = memory_path_for_namespace(self.base_path, namespace)
        if not path.exists():
            memory = create_default_memory()
            self._save_memory(namespace, memory)
            return memory

        data = json.loads(path.read_text(encoding="utf-8"))
        return LongTermMemory(**data)

    def _save_memory(
        self,
        namespace: MemoryNamespace,
        memory: LongTermMemory,
    ) -> None:
        path = memory_path_for_namespace(self.base_path, namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary_path.write_text(
                json.dumps(_model_to_dict(memory), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


class LangGraphMemoryRepository:
    def __init__(self, store):
        self.store = store

    def list_items(self, namespace: MemoryNamespace) -> list[MemoryItem]:
        items = [
            _memory_item_from_store(stored)
            for stored in self.store.search(namespace)
        ]
        return sorted(items, key=lambda item: item.created_at)

    def get_item(
        self,
        namespace: MemoryNamespace,
        item_id: str,
    ) -> MemoryItem | None:
        stored = self.store.get(namespace, item_id)
        if stored is None:
            return None
        return _memory_item_from_store(stored)

    def upsert_item(self, namespace: MemoryNamespace, item: MemoryItem) -> None:
        self.store.put(namespace, item.id, _model_to_dict(item))

    def delete_item(self, namespace: MemoryNamespace, item_id: str) -> bool:
        if self.store.get(namespace, item_id) is None:
            return False
        self.store.delete(namespace, item_id)
        return True


def memory_path_for_namespace(base_path: Path, namespace: MemoryNamespace) -> Path:
    if namespace == ("users", "default", "memory"):
        return base_path

    if len(namespace) >= 2 and namespace[0] == "users":
        user_id = _safe_path_segment(namespace[1])
        return base_path.with_name(f"{base_path.stem}.{user_id}{base_path.suffix}")

    namespace_suffix = ".".join(_safe_path_segment(part) for part in namespace)
    return base_path.with_name(f"{base_path.stem}.{namespace_suffix}{base_path.suffix}")


def _safe_path_segment(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return normalized.strip("._") or "default"


def _memory_item_from_store(stored) -> MemoryItem:
    value = dict(_stored_value(stored))
    value.setdefault("id", _stored_key(stored))
    return MemoryItem(**value)


def _stored_value(stored) -> dict:
    if hasattr(stored, "value"):
        return stored.value
    if isinstance(stored, dict) and "value" in stored:
        return stored["value"]
    return stored


def _stored_key(stored) -> str:
    if hasattr(stored, "key"):
        return stored.key
    if isinstance(stored, dict):
        return str(stored.get("key", ""))
    return ""


def _model_to_dict(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return json.loads(model.json())
