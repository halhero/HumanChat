import re
from pathlib import Path
from typing import Protocol

from human_chat.config import Settings
from human_chat.memory_store import LongTermMemory, MemoryItem, load_memory, save_memory


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
        return load_memory(self.path)

    def save_memory(self, namespace: MemoryNamespace, memory: LongTermMemory) -> None:
        self._validate_namespace(namespace)
        save_memory(self.path, memory)

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
