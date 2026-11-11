import sqlite3

import pytest

from human_chat.config import Settings
from human_chat.memory.resources import open_memory_resource
from tests.test_memory_maintenance import remember


def test_sqlite_memory_resource_is_persistent(tmp_path):
    settings = Settings(memory_path=tmp_path / "memory.sqlite", memory_backend="sqlite", memory_semantic_enabled=False)

    with open_memory_resource(settings) as resource:
        assert resource.backend == "sqlite"
        assert resource.persistent
        assert resource.service._index_writer is resource.retriever._index_reader
        item = remember(resource.service, "持久记忆")
        assert resource.service.get_item(item.id) == item
        assert resource.retriever.has_documents()
        retriever = resource.retriever

    with pytest.raises(sqlite3.ProgrammingError):
        retriever.has_documents()


def test_memory_store_resource_uses_langgraph_store(tmp_path):
    settings = Settings(memory_path=tmp_path / "memory.sqlite", memory_backend="memory", memory_semantic_enabled=False)

    with open_memory_resource(settings) as resource:
        assert resource.backend == "memory"
        assert not resource.persistent
        assert resource.service._index_writer is resource.retriever._index_reader
        item = remember(resource.service, "运行时记忆")
        assert resource.service.get_item(item.id) == item
        assert resource.retriever.has_documents()
        retriever = resource.retriever

    with pytest.raises(sqlite3.ProgrammingError):
        retriever.has_documents()
