from langgraph.store.memory import InMemoryStore
import pytest
from datetime import datetime, timezone

from human_chat.memory.models import Assertion, Evidence, MemoryItem, MemoryVersion
from human_chat.memory.repository import (
    JsonMemoryRepository,
    LangGraphMemoryRepository,
    memory_path_for_namespace,
)


def test_json_repository_creates_empty_memory_file(tmp_path):
    namespace = ("users", "default", "memory")
    path = tmp_path / "memory.json"
    repository = JsonMemoryRepository(path)

    items = repository.list_items(namespace)

    assert path.exists()
    assert items == []


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


def test_langgraph_repository_reads_all_pages_and_exact_namespace():
    namespace = ("users", "test", "memory")
    store = InMemoryStore()
    repository = LangGraphMemoryRepository(store)
    items = [MemoryItem(text=f"memory {index}") for index in range(115)]
    for item in items:
        repository.upsert_item(namespace, item)
    repository.upsert_item((*namespace, "child"), MemoryItem(text="other scope"))

    assert repository.list_items(namespace) == items


@pytest.mark.parametrize("backend", ["json", "store"])
def test_complete_versioned_record_survives_storage_round_trip(tmp_path, backend):
    store = InMemoryStore()
    path = tmp_path / "memory.json"
    repository = JsonMemoryRepository(path) if backend == "json" else LangGraphMemoryRepository(store)
    namespace = ("users", "default", "memory")
    evidence = Evidence(message_id="u1", user_id="default", session_id="s1",
                        message_time=datetime(2026, 1, 1, tzinfo=timezone.utc), quote="目标暂停")
    item = MemoryItem(text="记忆模块暂停", user_id="default", version=2,
                      assertion=Assertion(subject="HumanChat", relation="目标", value="记忆模块", state="暂停"),
                      evidence=[evidence], receipts={"operation-1": "digest"},
                      history=[MemoryVersion(version=1, text="记忆模块进行中", status="historical")])
    repository.upsert_item(namespace, item)
    reopened = JsonMemoryRepository(path) if backend == "json" else LangGraphMemoryRepository(store)
    assert reopened.get_item(namespace, item.id) == item
