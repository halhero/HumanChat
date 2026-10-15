from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from human_chat.config import Settings
from human_chat.memory.repository import (
    JsonMemoryRepository,
    LangGraphMemoryRepository,
    default_memory_namespace,
    memory_path_for_namespace,
)
from human_chat.memory.service import LongTermMemoryService
from human_chat.memory.index import MemoryIndex, create_embeddings


@dataclass(frozen=True)
class MemoryResource:
    service: LongTermMemoryService
    backend: str
    persistent: bool
    index: MemoryIndex | None = None


def _resource(settings, repository, namespace, backend, persistent):
    service = LongTermMemoryService(repository, namespace)
    index = None
    if settings.memory_semantic_enabled:
        path = memory_path_for_namespace(settings.memory_path, namespace).with_suffix(".index.json") if persistent else None
        configuration = f"{settings.memory_embedding_base_url or settings.llm_base_url}|{settings.memory_embedding_model}"
        index = MemoryIndex(create_embeddings(settings), path, configuration)
        service.attach_index(index)
    return MemoryResource(service, backend, persistent, index)


@contextmanager
def open_memory_resource(
    settings: Settings,
    backend: str | None = None,
) -> Iterator[MemoryResource]:
    selected_backend = (backend or settings.memory_backend).strip().lower()
    namespace = default_memory_namespace(settings)

    if selected_backend == "json":
        repository = JsonMemoryRepository(settings.memory_path)
        yield _resource(settings, repository, namespace, "json", True)
        return

    if selected_backend == "memory":
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()
        repository = LangGraphMemoryRepository(store)
        yield _resource(settings, repository, namespace, "memory", False)
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
            yield _resource(settings, repository, namespace, "postgres", True)
        return

    raise ValueError(f"不支持的长期记忆后端：{selected_backend}")
