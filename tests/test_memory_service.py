from human_chat.memory_models import LongTermMemory, MemoryItem
from human_chat.memory_service import LongTermMemoryService


class InMemoryRepository:
    def __init__(self):
        self.memory = LongTermMemory()

    def load_memory(self, namespace):
        return self.memory

    def save_memory(self, namespace, memory):
        self.memory = memory

    def list_items(self, namespace):
        return list(self.memory.items)

    def put_item(self, namespace, item):
        self.memory.items = [existing for existing in self.memory.items if existing.id != item.id]
        self.memory.items.append(item)

    def delete_item(self, namespace, item_id):
        original_count = len(self.memory.items)
        self.memory.items = [item for item in self.memory.items if item.id != item_id]
        return len(self.memory.items) != original_count


def create_service():
    return LongTermMemoryService(InMemoryRepository(), ("users", "test", "memory"))


def test_add_normalizes_and_deduplicates_memory_text():
    service = create_service()

    assert service.add("  用户喜欢中文讲解。  ")
    assert not service.add("用户喜欢中文讲解。")
    assert [item.text for item in service.load().items] == ["用户喜欢中文讲解。"]


def test_delete_uses_one_based_display_index():
    service = create_service()
    service.save(
        LongTermMemory(
            items=[
                MemoryItem(text="第一条"),
                MemoryItem(text="第二条"),
            ]
        )
    )

    assert service.delete(2) == "第二条"
    assert [item.text for item in service.load().items] == ["第一条"]
    assert service.delete(2) is None


def test_format_for_prompt_uses_repository_items():
    service = create_service()

    assert service.format_for_prompt() == "暂无长期记忆。"

    service.add("用户正在开发 HumanChat。")

    assert service.format_for_prompt() == "长期记忆：\n- 用户正在开发 HumanChat。"
