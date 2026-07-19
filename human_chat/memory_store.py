from human_chat.memory_models import LongTermMemory, MemoryItem


def add_memory_item(
    memory: LongTermMemory,
    text: str,
    source: str = "manual",
    confidence: float | None = None,
) -> bool:
    normalized = text.strip()
    items = get_memory_items(memory)
    if not normalized or normalized in items:
        return False
    memory.items.append(
        MemoryItem(
            text=normalized,
            source=source,
            confidence=confidence,
        )
    )
    return True


def delete_memory_item(memory: LongTermMemory, index: int) -> str | None:
    items = list(memory.items)
    zero_based_index = index - 1
    if zero_based_index < 0 or zero_based_index >= len(items):
        return None
    item = items[zero_based_index]
    memory.items = [existing for existing in memory.items if existing.id != item.id]
    return item.text


def get_memory_items(memory: LongTermMemory) -> list[str]:
    return [item.text for item in memory.items]


def format_memory_for_prompt(memory: LongTermMemory) -> str:
    items = get_memory_items(memory)
    if not items:
        return "暂无长期记忆。"
    return "\n".join(["长期记忆：", *[f"- {item}" for item in items]])
