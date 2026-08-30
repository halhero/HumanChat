from contextlib import contextmanager
from typing import Any, Iterator

from human_chat.checkpointing import CheckpointerResource, open_checkpointer
from human_chat.config import Settings
from human_chat.graph import build_graph
from human_chat.logging_config import get_logger
from human_chat.memory_resources import MemoryResource, open_memory_resource
from human_chat.memory_service import MemoryService
from human_chat.session_models import SessionRecord, now_local
from human_chat.session_repository import SessionRepository
from human_chat.storage import create_session_repository
from human_chat.tool_provider import ToolRegistry
from human_chat.tool_resources import open_tool_registry


logger = get_logger(__name__)


class ChatRuntime:
    def __init__(
        self,
        settings: Settings,
        session: SessionRecord,
        app,
        persist_session: bool = True,
        session_repository: SessionRepository | None = None,
        checkpoint_backend: str = "memory",
        checkpoint_persistent: bool = False,
        memory_service: MemoryService | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.settings = settings
        self.session = session
        self.app = app
        self.persist_session = persist_session
        self.session_repository = session_repository
        self.checkpoint_backend = checkpoint_backend
        self.checkpoint_persistent = checkpoint_persistent
        self.memory_service = memory_service
        self.tool_registry = tool_registry
        self.thread_id = session.thread_id
        self.graph_config = {"configurable": {"thread_id": self.thread_id}}
        self.messages = []

        if self.persist_session and self.session_repository is None:
            raise ValueError("持久化会话必须提供 SessionRepository。")

    def ask(self, question: str) -> dict:
        result = self.app.invoke(
            {"question": question},
            config=self.graph_config,
        )
        self._save_runtime_state(result, question=question)
        return result

    def resume(self, value: dict) -> dict:
        from langgraph.types import Command

        result = self.app.invoke(
            Command(resume=value),
            config=self.graph_config,
        )
        self._save_runtime_state(result)
        return result

    def stream(
        self,
        question: str,
        *,
        control: Any | None = None,
    ) -> Iterator[tuple[str, Any]]:
        """Stream Graph state updates while preserving session metadata."""

        yield from self._stream_graph(
            {"question": question},
            question=question,
            control=control,
        )

    def resume_stream(
        self,
        value: dict,
        *,
        control: Any | None = None,
    ) -> Iterator[tuple[str, Any]]:
        """Resume an interrupted Graph and stream the remaining work."""

        from langgraph.types import Command

        yield from self._stream_graph(
            Command(resume=value),
            control=control,
        )

    def _stream_graph(
        self,
        graph_input,
        *,
        question: str | None = None,
        control: Any | None = None,
    ) -> Iterator[tuple[str, Any]]:
        latest_state: dict = {}
        for mode, data in self.app.stream(
            graph_input,
            config=self.graph_config,
            stream_mode=["updates", "values"],
            durability="sync",
            control=control,
        ):
            if mode == "values" and isinstance(data, dict):
                latest_state = data
            yield mode, data

        if latest_state:
            self._save_runtime_state(latest_state, question=question)

    def _save_runtime_state(
        self,
        result: dict,
        *,
        question: str | None = None,
    ) -> None:
        self.messages = result.get("messages", self.messages)

        if self.persist_session:
            title = self.session.title
            if question and title == "新对话":
                title = _session_title(question)
            self.session = self.session.model_copy(
                update={
                    "title": title,
                    "message_count": len(self.messages),
                    "updated_at": now_local(),
                    "checkpoint_backend": self.checkpoint_backend,
                    "recoverable": self.checkpoint_persistent,
                }
            )
            self.session_repository.save(self.session)


class ChatApplication:
    """Own shared Agent resources and create lightweight per-session runtimes.

    A web process can serve many HTTP requests during its lifetime. Opening the
    checkpointer, memory store, MCP providers, and compiled graph for every request would
    repeatedly create database connections and rediscover remote tools. This object keeps
    those expensive resources at application scope while preserving session isolation
    through each runtime's LangGraph ``thread_id``.

    Resource cleanup remains the responsibility of ``open_chat_application``.
    """

    def __init__(
        self,
        settings: Settings,
        app,
        session_repository: SessionRepository,
        checkpoint: CheckpointerResource,
        memory: MemoryResource,
        tool_registry: ToolRegistry,
    ):
        self.settings = settings
        self.app = app
        self.session_repository = session_repository
        self.checkpoint = checkpoint
        self.memory = memory
        self.tool_registry = tool_registry

    def create_runtime(
        self,
        session: SessionRecord | None = None,
        *,
        persist_session: bool = True,
    ) -> ChatRuntime:
        """Create a session-scoped view over the shared compiled graph."""

        active_session = session or SessionRecord.create()
        active_session = _synchronize_session_recovery(
            active_session,
            self.checkpoint,
            self.session_repository,
            persist_session,
        )
        return ChatRuntime(
            settings=self.settings,
            session=active_session,
            app=self.app,
            persist_session=persist_session,
            session_repository=(
                self.session_repository if persist_session else None
            ),
            checkpoint_backend=self.checkpoint.backend,
            checkpoint_persistent=self.checkpoint.persistent,
            memory_service=self.memory.service,
            tool_registry=self.tool_registry,
        )


@contextmanager
def open_chat_application(
    settings: Settings,
    *,
    session_repository: SessionRepository | None = None,
    checkpoint_backend: str | None = None,
    enable_server_audio: bool = True,
) -> Iterator[ChatApplication]:
    """Open process-scoped resources used by CLI or HTTP application lifetimes."""

    repository = session_repository or create_session_repository(settings)
    with open_checkpointer(settings, backend=checkpoint_backend) as checkpoint:
        with open_memory_resource(settings) as memory:
            with open_tool_registry(settings) as tool_registry:
                app = _build_runtime_graph(
                    settings,
                    checkpoint,
                    memory,
                    tool_registry,
                    enable_server_audio=enable_server_audio,
                )
                yield ChatApplication(
                    settings=settings,
                    app=app,
                    session_repository=repository,
                    checkpoint=checkpoint,
                    memory=memory,
                    tool_registry=tool_registry,
                )


@contextmanager
def open_chat_runtime(
    settings: Settings,
    session: SessionRecord | None = None,
    persist_session: bool = True,
    session_repository: SessionRepository | None = None,
    checkpoint_backend: str | None = None,
) -> Iterator[ChatRuntime]:
    repository = session_repository
    if persist_session and repository is None:
        repository = create_session_repository(settings)

    # The CLI still receives one context-managed runtime, while the same resource owner can
    # now be reused by a long-running FastAPI lifespan.
    with open_chat_application(
        settings,
        session_repository=repository,
        checkpoint_backend=checkpoint_backend,
    ) as application:
        yield application.create_runtime(
            session,
            persist_session=persist_session,
        )


def _build_runtime_graph(
    settings: Settings,
    checkpoint: CheckpointerResource,
    memory: MemoryResource,
    tool_registry: ToolRegistry,
    *,
    enable_server_audio: bool = True,
):
    return build_graph(
        settings,
        checkpointer=checkpoint.saver,
        memory_service=memory.service,
        tool_registry=tool_registry,
        enable_tts=enable_server_audio,
    )


def _synchronize_session_recovery(
    session: SessionRecord,
    checkpoint: CheckpointerResource,
    repository: SessionRepository | None,
    persist_session: bool,
) -> SessionRecord:
    has_existing_state = (
        checkpoint.persistent
        and checkpoint.has_thread(session.thread_id)
    )
    recoverable = has_existing_state

    if session.message_count > 0 and not recoverable:
        logger.warning(
            "Session %s has %s recorded messages but is not recoverable "
            "with the %s checkpointer; it will resume without history.",
            session.id,
            session.message_count,
            checkpoint.backend,
        )

    updates = {}
    if session.checkpoint_backend != checkpoint.backend:
        updates["checkpoint_backend"] = checkpoint.backend
    if session.recoverable != recoverable:
        updates["recoverable"] = recoverable

    if not updates:
        return session

    updated = session.model_copy(update=updates)
    if persist_session and repository is not None:
        repository.save(updated)
    return updated


def _session_title(question: str, max_length: int = 48) -> str:
    normalized = " ".join(question.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length].rstrip()}..."
