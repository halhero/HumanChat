from threading import RLock
from typing import Protocol

from human_chat.memory_models import LongTermMemory, MemoryItem
from human_chat.memory_repository import MemoryNamespace, MemoryRepository


class MemoryService(Protocol):
    def load(self) -> LongTermMemory:
        ...

    def add(
        self,
        text: str,
        source: str = "manual",
        confidence: float | None = None,
    ) -> MemoryItem | None:
        ...

    def delete_by_id(self, item_id: str) -> MemoryItem | None:
        ...

    def format_for_prompt(self) -> str:
        ...


class LongTermMemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        namespace: MemoryNamespace,
    ):
        self._repository = repository
        self._namespace = namespace
        self._lock = RLock()

    def load(self) -> LongTermMemory:
        with self._lock:
            items = self._repository.list_items(self._namespace)
        return LongTermMemory(items=items)

    def add(
        self,
        text: str,
        source: str = "manual",
        confidence: float | None = None,
    ) -> MemoryItem | None:
        normalized = text.strip()
        if not normalized:
            return None

        with self._lock:
            items = self._repository.list_items(self._namespace)
            if normalized in [item.text for item in items]:
                return None

            item = MemoryItem(
                text=normalized,
                source=source,
                confidence=confidence,
            )
            self._repository.upsert_item(self._namespace, item)
        return item

    def delete_by_id(self, item_id: str) -> MemoryItem | None:
        normalized = item_id.strip()
        if not normalized:
            return None

        with self._lock:
            item = self._repository.get_item(self._namespace, normalized)
            if item is None:
                return None
            if not self._repository.delete_item(self._namespace, item.id):
                return None
        return item

    def format_for_prompt(self) -> str:
        with self._lock:
            items = [
                item.text
                for item in self._repository.list_items(self._namespace)
            ]
        if not items:
            return "暂无长期记忆。"
        return "\n".join(["长期记忆：", *[f"- {item}" for item in items]])
