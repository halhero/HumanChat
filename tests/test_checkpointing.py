import sqlite3

import pytest

from human_chat.checkpointing import open_checkpointer
from human_chat.config import Settings


def test_memory_checkpointer_is_explicitly_non_persistent(tmp_path):
    settings = Settings(checkpoint_path=tmp_path / "checkpoint.sqlite")

    with open_checkpointer(settings, backend="memory") as resource:
        assert resource.backend == "memory"
        assert not resource.persistent
        assert not resource.has_thread("missing")


def test_sqlite_checkpointer_connection_closes_with_context(tmp_path):
    settings = Settings(checkpoint_path=tmp_path / "checkpoint.sqlite")

    with open_checkpointer(settings, backend="sqlite") as resource:
        connection = resource.saver.conn
        connection.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_unknown_checkpointer_backend_is_rejected(tmp_path):
    settings = Settings(checkpoint_path=tmp_path / "checkpoint.sqlite")

    with pytest.raises(ValueError):
        with open_checkpointer(settings, backend="unknown"):
            pass
