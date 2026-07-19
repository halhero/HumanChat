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
    def load_memory(self, namespace: MemoryNamespace) -> LongTermMemory:
        ...

    def save_memory(self, namespace: MemoryNamespace, memory: LongTermMemory) -> None:
        ...

    def list_items(self, namespace: MemoryNamespace) -> list[MemoryItem]:
        ...

    def put_item(self, namespace: MemoryNamespace, item: MemoryItem) -> None:
        ...

    def delete_item(self, namespace: MemoryNamespace, item_id: str) -> bool:
        ...


class JsonMemoryRepository:
    def __init__(self, path: Path, namespace: MemoryNamespace):
        self.path = memory_path_for_namespace(path, namespace)
        self.namespace = namespace

    def load_memory(self, namespace: MemoryNamespace) -> LongTermMemory:
        self._validate_namespace(namespace)
        if not self.path.exists():
            memory = create_default_memory()
            self.save_memory(namespace, memory)
            return memory

        data = json.loads(self.path.read_text(encoding="utf-8"))
        return LongTermMemory(**data)

    def save_memory(self, namespace: MemoryNamespace, memory: LongTermMemory) -> None:
        self._validate_namespace(namespace)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary_path.write_text(
                json.dumps(_memory_to_dict(memory), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def list_items(self, namespace: MemoryNamespace) -> list[MemoryItem]:
        return list(self.load_memory(namespace).items)

    def put_item(self, namespace: MemoryNamespace, item: MemoryItem) -> None:
        memory = self.load_memory(namespace)
        memory.items = [existing for existing in memory.items if existing.id != item.id]
        memory.items.append(item)
        self.save_memory(namespace, memory)

    def delete_item(self, namespace: MemoryNamespace, item_id: str) -> bool:
        memory = self.load_memory(namespace)
        original_count = len(memory.items)
        memory.items = [item for item in memory.items if item.id != item_id]
        deleted = len(memory.items) != original_count
        if deleted:
            self.save_memory(namespace, memory)
        return deleted

    def _validate_namespace(self, namespace: MemoryNamespace) -> None:
        if namespace != self.namespace:
            raise ValueError(f"JSON memory repository only supports namespace: {self.namespace}")


class LangGraphMemoryRepository:
    def __init__(self, store):
        self.store = store

    def load_memory(self, namespace: MemoryNamespace) -> LongTermMemory:
        stored = self.store.get(namespace, "profile")
        if stored is None:
            return LongTermMemory()
        return LongTermMemory(**_stored_value(stored))

    def save_memory(self, namespace: MemoryNamespace, memory: LongTermMemory) -> None:
        self.store.put(namespace, "profile", _memory_to_dict(memory))

    def list_items(self, namespace: MemoryNamespace) -> list[MemoryItem]:
        return list(self.load_memory(namespace).items)

    def put_item(self, namespace: MemoryNamespace, item: MemoryItem) -> None:
        memory = self.load_memory(namespace)
        memory.items = [existing for existing in memory.items if existing.id != item.id]
        memory.items.append(item)
        self.save_memory(namespace, memory)

    def delete_item(self, namespace: MemoryNamespace, item_id: str) -> bool:
        memory = self.load_memory(namespace)
        original_count = len(memory.items)
        memory.items = [item for item in memory.items if item.id != item_id]
        deleted = len(memory.items) != original_count
        if deleted:
            self.save_memory(namespace, memory)
        return deleted


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


def _stored_value(stored) -> dict:
    if hasattr(stored, "value"):
        return stored.value
    if isinstance(stored, dict) and "value" in stored:
        return stored["value"]
    return stored


def _memory_to_dict(memory: LongTermMemory) -> dict:
    if hasattr(memory, "model_dump"):
        return memory.model_dump()
    return memory.dict()
