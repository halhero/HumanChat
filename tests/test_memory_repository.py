from langgraph.store.memory import InMemoryStore

from human_chat.memory_models import MemoryItem
from human_chat.memory_repository import (
    JsonMemoryRepository,
    LangGraphMemoryRepository,
    memory_path_for_namespace,
)


def test_json_repository_creates_default_items(tmp_path):
    namespace = ("users", "default", "memory")
    path = tmp_path / "memory.json"
    repository = JsonMemoryRepository(path)

    items = repository.list_items(namespace)

    assert path.exists()
    assert items


def test_json_repository_item_round_trip(tmp_path):
    namespace = ("users", "test-user", "memory")
    repository = JsonMemoryRepository(tmp_path / "memory.json")
    item = MemoryItem(text="测试记忆")

    repository.upsert_item(namespace, item)

    assert repository.get_item(namespace, item.id) == item
    assert [stored.text for stored in repository.list_items(namespace)][-1] == "测试记忆"
    assert memory_path_for_namespace(repository.base_path, namespace) == (
        tmp_path / "memory.test-user.json"
    )


def test_json_repository_deletes_by_item_id(tmp_path):
    namespace = ("users", "test", "memory")
    repository = JsonMemoryRepository(tmp_path / "memory.json")
    item = MemoryItem(text="待删除")
    repository.upsert_item(namespace, item)

    assert repository.delete_item(namespace, item.id)
    assert repository.get_item(namespace, item.id) is None
    assert not repository.delete_item(namespace, item.id)


def test_langgraph_repository_stores_each_memory_under_its_id():
    namespace = ("users", "test", "memory")
    store = InMemoryStore()
    repository = LangGraphMemoryRepository(store)
    item = MemoryItem(text="框架记忆")

    repository.upsert_item(namespace, item)

    assert store.get(namespace, item.id).value["text"] == "框架记忆"
    assert repository.get_item(namespace, item.id) == item
    assert repository.list_items(namespace) == [item]
    assert repository.delete_item(namespace, item.id)


def test_memory_path_sanitizes_user_namespace(tmp_path):
    path = memory_path_for_namespace(
        tmp_path / "memory.json",
        ("users", "../unsafe user", "memory"),
    )

    assert path.parent == tmp_path
    assert path.name == "memory.unsafe_user.json"
