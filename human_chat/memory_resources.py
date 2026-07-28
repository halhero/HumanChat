from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from human_chat.config import Settings
from human_chat.memory_repository import (
    JsonMemoryRepository,
    LangGraphMemoryRepository,
    MemoryRepository,
    default_memory_namespace,
)
from human_chat.memory_service import LongTermMemoryService, MemoryService


@dataclass(frozen=True)
class MemoryResource:
    repository: MemoryRepository
    service: MemoryService
    store: Any | None
    backend: str
    persistent: bool


@contextmanager
def open_memory_resource(
    settings: Settings,
    backend: str | None = None,
) -> Iterator[MemoryResource]:
    selected_backend = (backend or settings.memory_backend).strip().lower()
    namespace = default_memory_namespace(settings)

    if selected_backend == "json":
        repository = JsonMemoryRepository(settings.memory_path)
        yield MemoryResource(
            repository=repository,
            service=LongTermMemoryService(repository, namespace),
            store=None,
            backend="json",
            persistent=True,
        )
        return

    if selected_backend == "memory":
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()
        repository = LangGraphMemoryRepository(store)
        yield MemoryResource(
            repository=repository,
            service=LongTermMemoryService(repository, namespace),
            store=store,
            backend="memory",
            persistent=False,
        )
        return

    if selected_backend == "postgres":
        if not settings.memory_postgres_uri:
            raise RuntimeError(
                "HUMANCHAT_MEMORY_POSTGRES_URI 未配置，无法启用 PostgresStore。"
            )
        try:
            from langgraph.store.postgres import PostgresStore
        except ImportError as exc:
            raise RuntimeError(
                "PostgresStore 需要安装 langgraph-checkpoint-postgres 和 psycopg。"
            ) from exc

        with PostgresStore.from_conn_string(settings.memory_postgres_uri) as store:
            store.setup()
            repository = LangGraphMemoryRepository(store)
            yield MemoryResource(
                repository=repository,
                service=LongTermMemoryService(repository, namespace),
                store=store,
                backend="postgres",
                persistent=True,
            )
        return

    raise ValueError(f"不支持的长期记忆后端：{selected_backend}")
