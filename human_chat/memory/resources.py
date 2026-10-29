from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from human_chat.config import Settings
from human_chat.memory.repository import (
    LangGraphMemoryRepository,
    SqliteMemoryRepository,
    default_memory_namespace,
)
from human_chat.memory.search import (
    MemorySearch,
    PostgresMemorySearch,
    SqliteMemorySearch,
    create_embeddings,
)
from human_chat.memory.service import LongTermMemoryService


@dataclass(frozen=True)
class MemoryResource:
    service: LongTermMemoryService
    search: MemorySearch
    backend: str
    persistent: bool


def memory_sidecar_path(memory_path: Path, name: str) -> Path:
    return memory_path.with_name(f"{memory_path.stem}.{name}.sqlite")


def _embedding_configuration(settings: Settings) -> str:
    return f"{settings.memory_embedding_base_url or settings.llm_base_url}|{settings.memory_embedding_model}"


def _embeddings(settings: Settings):
    return create_embeddings(settings) if settings.memory_semantic_enabled else None


@contextmanager
def open_memory_resource(
    settings: Settings,
    backend: str | None = None,
) -> Iterator[MemoryResource]:
    selected_backend = (backend or settings.memory_backend).strip().lower()
    namespace = default_memory_namespace(settings)
    configuration = _embedding_configuration(settings)

    if selected_backend == "sqlite":
        repository = SqliteMemoryRepository(settings.memory_path)
        search = SqliteMemorySearch(
            memory_sidecar_path(settings.memory_path, "search"),
            _embeddings(settings),
            configuration,
        )
        try:
            yield MemoryResource(
                LongTermMemoryService(repository, namespace, search),
                search,
                "sqlite",
                True,
            )
        finally:
            search.close()
            repository.close()
        return

    if selected_backend == "memory":
        from langgraph.store.memory import InMemoryStore

        search = SqliteMemorySearch(None, _embeddings(settings), configuration)
        try:
            yield MemoryResource(
                LongTermMemoryService(LangGraphMemoryRepository(InMemoryStore()), namespace, search),
                search,
                "memory",
                False,
            )
        finally:
            search.close()
        return

    if selected_backend == "postgres":
        if not settings.memory_postgres_uri:
            raise RuntimeError("HUMANCHAT_MEMORY_POSTGRES_URI 未配置，无法启用 PostgresStore。")
        try:
            from langgraph.store.postgres import PostgresStore
        except ImportError as exc:
            raise RuntimeError(
                "PostgresStore 需要安装 langgraph-checkpoint-postgres 和 psycopg。"
            ) from exc

        search = PostgresMemorySearch(
            settings.memory_postgres_uri,
            namespace,
            _embeddings(settings),
            configuration,
        )
        try:
            with PostgresStore.from_conn_string(settings.memory_postgres_uri) as store:
                store.setup()
                yield MemoryResource(
                    LongTermMemoryService(LangGraphMemoryRepository(store), namespace, search),
                    search,
                    "postgres",
                    True,
                )
        finally:
            search.close()
        return

    raise ValueError(f"不支持的长期记忆后端：{selected_backend}")
