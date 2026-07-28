from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from human_chat.config import Settings
from human_chat.logging_config import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True)
class CheckpointerResource:
    saver: Any
    backend: str
    persistent: bool

    def has_thread(self, thread_id: str) -> bool:
        config = {"configurable": {"thread_id": thread_id}}
        return self.saver.get_tuple(config) is not None


@contextmanager
def open_checkpointer(
    settings: Settings,
    backend: str | None = None,
) -> Iterator[CheckpointerResource]:
    selected_backend = (backend or settings.checkpoint_backend).strip().lower()

    if selected_backend == "memory":
        yield CheckpointerResource(
            saver=_create_memory_checkpointer(),
            backend="memory",
            persistent=False,
        )
        return

    if selected_backend != "sqlite":
        raise ValueError(f"不支持的 Checkpointer 后端：{selected_backend}")

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        if settings.checkpoint_allow_memory_fallback:
            logger.warning(
                "SQLite checkpointer is unavailable; using explicitly allowed in-memory fallback."
            )
            yield CheckpointerResource(
                saver=_create_memory_checkpointer(),
                backend="memory",
                persistent=False,
            )
            return
        raise RuntimeError(
            "langgraph-checkpoint-sqlite 未安装，且未允许内存 Checkpointer 降级。"
        ) from exc

    path = _prepare_sqlite_path(settings.checkpoint_path)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        logger.info("Using managed SQLite LangGraph checkpointer: %s", path)
        yield CheckpointerResource(
            saver=saver,
            backend="sqlite",
            persistent=True,
        )


def _prepare_sqlite_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _create_memory_checkpointer():
    try:
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
