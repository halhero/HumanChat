from human_chat.memory.models import MemoryItem
from human_chat.memory.search import SqliteMemorySearch
from human_chat.memory.service import LongTermMemoryService
from tests.test_memory_maintenance import operation, remember

TEST_NAMESPACE = ("users", "default", "memory")


class InMemoryRepository:
    def __init__(self):
        self.items = {}
        self.requested_ids = []

    def get_item(self, namespace, item_id):
        self.requested_ids.append(item_id)
        return self.items.get(item_id)

    def get_items(self, namespace, item_ids):
        self.requested_ids.extend(item_ids)
        return [self.items[item_id] for item_id in item_ids if item_id in self.items]

    def upsert_item(self, namespace, item):
        self.items[item.id] = item

    def delete_item(self, namespace, item_id):
        return self.items.pop(item_id, None) is not None


def create_service():
    repository = InMemoryRepository()
    search = SqliteMemorySearch(None)
    return LongTermMemoryService(repository, TEST_NAMESPACE, search), repository, search


def test_delete_reads_only_the_target_and_reuses_stable_memory_id():
    service, repository, search = create_service()
    first = remember(service, "第一条")
    second = remember(service, "第二条")
    repository.requested_ids.clear()

    deletion = operation("delete-second", second=2, kind="delete", target=second)
    assert service.apply(deletion).outcome == "applied"
    assert repository.requested_ids == [second.id]
    assert service.get_item(first.id).status == "active"
    assert service.apply(deletion).outcome == "already_applied"
    search.close()


def test_get_items_preserves_bounded_repository_contract():
    service, repository, search = create_service()
    first = MemoryItem(text="一", user_id="default")
    second = MemoryItem(text="二", user_id="default")
    repository.items = {first.id: first, second.id: second}

    assert service.get_items([second.id]) == [second]
    assert repository.requested_ids == [second.id]
    search.close()
