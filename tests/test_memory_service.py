from human_chat.memory_models import MemoryItem
from human_chat.memory_service import LongTermMemoryService


TEST_NAMESPACE = ("users", "test", "memory")


class InMemoryRepository:
    def __init__(self):
        self.items = {}

    def list_items(self, namespace):
        return list(self.items.values())

    def get_item(self, namespace, item_id):
        return self.items.get(item_id)

    def upsert_item(self, namespace, item):
        self.items[item.id] = item

    def delete_item(self, namespace, item_id):
        return self.items.pop(item_id, None) is not None


def create_service():
    repository = InMemoryRepository()
    service = LongTermMemoryService(repository, TEST_NAMESPACE)
    return service, repository


def test_add_normalizes_and_deduplicates_memory_text():
    service, _ = create_service()

    assert service.add("  用户喜欢中文讲解。  ")
    assert not service.add("用户喜欢中文讲解。")
    assert [item.text for item in service.load().items] == ["用户喜欢中文讲解。"]


def test_delete_uses_one_based_display_index():
    service, repository = create_service()
    first = MemoryItem(text="第一条")
    second = MemoryItem(text="第二条")
    repository.upsert_item(TEST_NAMESPACE, first)
    repository.upsert_item(TEST_NAMESPACE, second)

    assert service.delete(2) == "第二条"
    assert [item.text for item in service.load().items] == ["第一条"]
    assert service.delete(2) is None


def test_format_for_prompt_uses_repository_items():
    service, _ = create_service()

    assert service.format_for_prompt() == "暂无长期记忆。"

    service.add("用户正在开发 HumanChat。")

    assert service.format_for_prompt() == "长期记忆：\n- 用户正在开发 HumanChat。"
