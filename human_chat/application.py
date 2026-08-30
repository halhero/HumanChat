"""Process-scoped HumanChat application and managed resource composition."""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from langgraph.types import Command

from human_chat.checkpointing import CheckpointerResource, open_checkpointer
from human_chat.config import Settings
from human_chat.graph import build_graph
from human_chat.logging_config import get_logger
from human_chat.memory_resources import MemoryResource, open_memory_resource
from human_chat.session_models import SessionRecord, now_local
from human_chat.session_repository import SessionRepository
from human_chat.storage import create_session_repository
from human_chat.tool_provider import ToolRegistry
from human_chat.tool_resources import open_tool_registry


logger = get_logger(__name__)


@dataclass(frozen=True)
class ApplicationStatus:
    """Safe operational data that interface adapters may expose."""

    checkpoint_backend: str
    checkpoint_persistent: bool
    memory_backend: str
    memory_persistent: bool
    mcp_enabled: bool
    registered_tool_count: int


class HumanChatApplication:
    """Own backend resources and expose interface-neutral conversation use cases.

    Repository and framework resource objects deliberately remain private. FastAPI and
    future adapters call application methods instead of coordinating storage or LangGraph
    directly.
    """

    def __init__(
        self,
        settings: Settings,
        graph,
        session_repository: SessionRepository,
        checkpoint: CheckpointerResource,
        memory: MemoryResource,
        tool_registry: ToolRegistry,
    ) -> None:
        self._settings = settings
        self._graph = graph
        self._sessions = session_repository
        self._checkpoint = checkpoint
        self._memory = memory
        self._tool_registry = tool_registry

    def status(self) -> ApplicationStatus:
        return ApplicationStatus(
            checkpoint_backend=self._checkpoint.backend,
            checkpoint_persistent=self._checkpoint.persistent,
            memory_backend=self._memory.backend,
            memory_persistent=self._memory.persistent,
            mcp_enabled=self._settings.mcp_enabled,
            registered_tool_count=len(self._tool_registry.registrations()),
        )

    def create_session(self) -> SessionRecord:
        session = self._sessions.create()
        return self._synchronize_session_recovery(session)

    def get_session(self, session_id: str) -> SessionRecord:
        return self._synchronize_session_recovery(
            self._sessions.load(session_id)
        )

    def list_sessions(self, limit: int = 20) -> list[SessionRecord]:
        return [
            self._synchronize_session_recovery(session)
            for session in self._sessions.list_recent(limit=limit)
        ]

    def run_turn(self, session_id: str, question: str) -> dict:
        session = self.get_session(session_id)
        result = self._graph.invoke(
            {"question": question},
            config=self._graph_config(session.thread_id),
        )
        self._save_session_state(session, result)
        return result

    def resume_turn(self, session_id: str, value: dict) -> dict:
        session = self.get_session(session_id)
        result = self._graph.invoke(
            Command(resume=value),
            config=self._graph_config(session.thread_id),
        )
        self._save_session_state(session, result)
        return result

    def get_graph_state(self, session_id: str):
        session = self.get_session(session_id)
        return self._graph.get_state(
            self._graph_config(session.thread_id)
        )

    @staticmethod
    def _graph_config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _save_session_state(self, session: SessionRecord, result: dict) -> None:
        messages = result.get("messages", [])
        updated = session.model_copy(
            update={
                "message_count": len(messages),
                "updated_at": now_local(),
                "checkpoint_backend": self._checkpoint.backend,
                "recoverable": self._checkpoint.persistent,
            }
        )
        self._sessions.save(updated)

    def _synchronize_session_recovery(
        self,
        session: SessionRecord,
    ) -> SessionRecord:
        recoverable = (
            self._checkpoint.persistent
            and self._checkpoint.has_thread(session.thread_id)
        )
        updates = {}
        if session.checkpoint_backend != self._checkpoint.backend:
            updates["checkpoint_backend"] = self._checkpoint.backend
        if session.recoverable != recoverable:
            updates["recoverable"] = recoverable
        if not updates:
            return session

        if session.message_count > 0 and not recoverable:
            logger.warning(
                "Session %s has metadata but no recoverable checkpoint state.",
                session.id,
            )
        updated = session.model_copy(update=updates)
        self._sessions.save(updated)
        return updated


@contextmanager
def open_human_chat_application(
    settings: Settings,
    *,
    session_repository: SessionRepository | None = None,
    checkpoint_backend: str | None = None,
) -> Iterator[HumanChatApplication]:
    """Open every process-scoped resource in dependency order."""

    repository = session_repository or create_session_repository(settings)
    with open_checkpointer(settings, backend=checkpoint_backend) as checkpoint:
        with open_memory_resource(settings) as memory:
            with open_tool_registry(settings) as tool_registry:
                graph = build_graph(
                    settings,
                    checkpointer=checkpoint.saver,
                    memory_service=memory.service,
                    tool_registry=tool_registry,
                )
                yield HumanChatApplication(
                    settings=settings,
                    graph=graph,
                    session_repository=repository,
                    checkpoint=checkpoint,
                    memory=memory,
                    tool_registry=tool_registry,
                )
