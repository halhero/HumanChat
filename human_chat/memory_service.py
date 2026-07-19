from typing import Protocol

from human_chat.memory_models import LongTermMemory, MemoryItem
from human_chat.memory_repository import MemoryNamespace, MemoryRepository


class MemoryService(Protocol):
    def load(self) -> LongTermMemory:
        ...

    def save(self, memory: LongTermMemory) -> None:
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
        self.repository = repository
        self.namespace = namespace

    def load(self) -> LongTermMemory:
        return self.repository.load_memory(self.namespace)

    def save(self, memory: LongTermMemory) -> None:
        self.repository.save_memory(self.namespace, memory)

    def add(
        self,
        text: str,
        source: str = "manual",
        confidence: float | None = None,
    ) -> bool:
        normalized = text.strip()
        if not normalized:
            return False

        items = self.repository.list_items(self.namespace)
        if normalized in [item.text for item in items]:
            return False

        self.repository.put_item(
            self.namespace,
            MemoryItem(
                text=normalized,
                source=source,
                confidence=confidence,
            ),
        )
        return True

    def delete(self, index: int) -> str | None:
        items = self.repository.list_items(self.namespace)
        zero_based_index = index - 1
        if zero_based_index < 0 or zero_based_index >= len(items):
            return None

        item = items[zero_based_index]
        if not self.repository.delete_item(self.namespace, item.id):
            return None
        return item.text

    def format_for_prompt(self) -> str:
        items = [item.text for item in self.repository.list_items(self.namespace)]
        if not items:
            return "暂无长期记忆。"
        return "\n".join(["长期记忆：", *[f"- {item}" for item in items]])
