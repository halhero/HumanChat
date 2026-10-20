from human_chat.memory.models import MemoryItem
from human_chat.memory.service import LongTermMemoryService
from tests.test_memory_maintenance import operation, remember


TEST_NAMESPACE = ("users", "default", "memory")


class InMemoryRepository:
    def __init__(self):
        self.items = {}

    def locked(self, namespace):
        from threading import RLock
        return RLock()

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


def test_add_rejects_duplicate_assertion_without_a_target():
    service, _ = create_service()

    item = remember(service, "用户喜欢中文讲解。")
    duplicate = operation("another-source", second=1, assertion=item.assertion)
    assert service.apply(duplicate).reason == "duplicate_requires_target"
    assert service.load().items == [item]


def test_delete_uses_stable_memory_id():
    service, _ = create_service()
    first = remember(service, "第一条")
    second = remember(service, "第二条")

    deletion = operation("delete-second", second=2, kind="delete", target=second)
    assert service.apply(deletion).outcome == "applied"
    assert service.load().items == [first]
    assert service.apply(deletion).outcome == "already_applied"


def test_load_returns_repository_items_without_prompt_formatting():
    service, repository = create_service()

    assert service.load().items == []

    repository.upsert_item(TEST_NAMESPACE, MemoryItem(text="用户正在开发 HumanChat。"))

    assert [item.text for item in service.load().items] == ["用户正在开发 HumanChat。"]
