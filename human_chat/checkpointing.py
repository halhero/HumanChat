from pathlib import Path

from human_chat.config import Settings
from human_chat.logging_config import get_logger


logger = get_logger(__name__)


def create_checkpointer(settings: Settings | None = None):
    if settings is not None:
        sqlite_checkpointer = _create_sqlite_checkpointer(settings.checkpoint_path)
        if sqlite_checkpointer is not None:
            return sqlite_checkpointer

    logger.warning("Using in-memory LangGraph checkpointer; chat state will not survive process restart.")
    return _create_memory_checkpointer()


def _create_sqlite_checkpointer(path: Path):
    try:
        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        logger.warning(
            "langgraph-checkpoint-sqlite is not installed; falling back to in-memory checkpointer."
        )
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    setup = getattr(checkpointer, "setup", None)
    if callable(setup):
        setup()
    logger.info("Using SQLite LangGraph checkpointer: %s", path)
    return checkpointer


def _create_memory_checkpointer():
    try:
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
