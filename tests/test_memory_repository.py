from datetime import datetime, timezone

import pytest
from langgraph.store.memory import InMemoryStore

from human_chat.memory.models import Assertion, Evidence, MemoryItem, MemoryVersion
from human_chat.memory.repository import (
    LangGraphMemoryRepository,
    SqliteMemoryRepository,
)


def test_sqlite_repository_reads_only_requested_items(tmp_path):
    namespace = ("users", "test-user", "memory")
    repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    first = MemoryItem(text="第一条")
    second = MemoryItem(text="第二条")
    repository.upsert_item(namespace, first)
    repository.upsert_item(namespace, second)

    assert repository.get_item(namespace, first.id) == first
    assert repository.get_items(namespace, [second.id, "missing", first.id]) == [second, first]
    repository.close()


def test_sqlite_repository_deletes_by_item_id(tmp_path):
    namespace = ("users", "test", "memory")
    repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    item = MemoryItem(text="待删除")
    repository.upsert_item(namespace, item)

    assert repository.delete_item(namespace, item.id)
    assert repository.get_item(namespace, item.id) is None
    assert not repository.delete_item(namespace, item.id)
    repository.close()


def test_langgraph_repository_stores_and_reads_supplied_ids_only():
    namespace = ("users", "test", "memory")
    store = InMemoryStore()
    repository = LangGraphMemoryRepository(store)
    first = MemoryItem(text="框架记忆一")
    second = MemoryItem(text="框架记忆二")
    repository.upsert_item(namespace, first)
    repository.upsert_item(namespace, second)

    assert store.get(namespace, first.id).value["text"] == "框架记忆一"
    assert repository.get_items(namespace, [second.id]) == [second]
    assert repository.delete_item(namespace, first.id)


@pytest.mark.parametrize("backend", ["sqlite", "store"])
def test_complete_versioned_record_survives_storage_round_trip(tmp_path, backend):
    store = InMemoryStore()
    path = tmp_path / "memory.sqlite"
    repository = (SqliteMemoryRepository(path) if backend == "sqlite"
                  else LangGraphMemoryRepository(store))
    namespace = ("users", "default", "memory")
    evidence = Evidence(
        message_id="u1",
        user_id="default",
        session_id="s1",
        message_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        quote="目标暂停",
    )
    item = MemoryItem(
        text="记忆模块暂停",
        user_id="default",
        version=2,
        assertion=Assertion(subject="HumanChat", relation="目标", value="记忆模块", state="暂停"),
        evidence=[evidence],
        last_operation_id="operation-1",
        history=[MemoryVersion(version=1, text="记忆模块进行中", status="historical")],
    )
    repository.upsert_item(namespace, item)
    if backend == "sqlite":
        repository.close()
        repository = SqliteMemoryRepository(path)
    assert repository.get_item(namespace, item.id) == item
    if backend == "sqlite":
        repository.close()
