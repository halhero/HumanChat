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
    ) -> bool:
        ...

    def delete(self, index: int) -> str | None:
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

    def load(self) -> LongTermMemory:
        return LongTermMemory(items=self._repository.list_items(self._namespace))

    def add(
        self,
        text: str,
        source: str = "manual",
        confidence: float | None = None,
    ) -> bool:
        normalized = text.strip()
        if not normalized:
            return False

        items = self._repository.list_items(self._namespace)
        if normalized in [item.text for item in items]:
            return False

        self._repository.upsert_item(
            self._namespace,
            MemoryItem(
                text=normalized,
                source=source,
                confidence=confidence,
            ),
        )
        return True

    def delete(self, index: int) -> str | None:
        items = self._repository.list_items(self._namespace)
        zero_based_index = index - 1
        if zero_based_index < 0 or zero_based_index >= len(items):
            return None

        item = items[zero_based_index]
        if not self._repository.delete_item(self._namespace, item.id):
            return None
        return item.text

    def format_for_prompt(self) -> str:
        items = [
            item.text
            for item in self._repository.list_items(self._namespace)
        ]
        if not items:
            return "暂无长期记忆。"
        return "\n".join(["长期记忆：", *[f"- {item}" for item in items]])
