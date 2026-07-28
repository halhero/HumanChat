from contextlib import contextmanager
from typing import Iterator

from human_chat.checkpointing import CheckpointerResource, open_checkpointer
from human_chat.config import Settings
from human_chat.graph import build_graph
from human_chat.logging_config import get_logger
from human_chat.session_models import SessionRecord, now_local
from human_chat.session_repository import SessionRepository
from human_chat.storage import create_session_repository


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
    ):
        self.settings = settings
        self.session = session
        self.app = app
        self.persist_session = persist_session
        self.session_repository = session_repository
        self.checkpoint_backend = checkpoint_backend
        self.checkpoint_persistent = checkpoint_persistent
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
        self._save_runtime_state(result)
        return result

    def resume(self, value: dict) -> dict:
        from langgraph.types import Command

        result = self.app.invoke(Command(resume=value), config=self.graph_config)
        self._save_runtime_state(result)
        return result

    def _save_runtime_state(self, result: dict) -> None:
        self.messages = result.get("messages", self.messages)

        if self.persist_session:
            self.session = self.session.model_copy(
                update={
                    "message_count": len(self.messages),
                    "updated_at": now_local(),
                    "checkpoint_backend": self.checkpoint_backend,
                    "recoverable": self.checkpoint_persistent,
                }
            )
            self.session_repository.save(self.session)


@contextmanager
def open_chat_runtime(
    settings: Settings,
    session: SessionRecord | None = None,
    persist_session: bool = True,
    session_repository: SessionRepository | None = None,
    checkpoint_backend: str | None = None,
) -> Iterator[ChatRuntime]:
    active_session = session or SessionRecord.create()
    repository = session_repository
    if persist_session and repository is None:
        repository = create_session_repository(settings)

    with open_checkpointer(settings, backend=checkpoint_backend) as checkpoint:
        active_session = _synchronize_session_recovery(
            active_session,
            checkpoint,
            repository,
            persist_session,
        )
        app = build_graph(settings, checkpointer=checkpoint.saver)
        yield ChatRuntime(
            settings=settings,
            session=active_session,
            app=app,
            persist_session=persist_session,
            session_repository=repository,
            checkpoint_backend=checkpoint.backend,
            checkpoint_persistent=checkpoint.persistent,
        )


def _synchronize_session_recovery(
    session: SessionRecord,
    checkpoint: CheckpointerResource,
    repository: SessionRepository | None,
    persist_session: bool,
) -> SessionRecord:
    has_existing_state = checkpoint.has_thread(session.thread_id)
    recoverable = checkpoint.persistent and (
        session.message_count == 0 or has_existing_state
    )

    if session.message_count > 0 and not has_existing_state:
        logger.warning(
            "Session %s has metadata but no checkpoint state; it will resume without history.",
            session.id,
        )

    updated = session.model_copy(
        update={
            "checkpoint_backend": checkpoint.backend,
            "recoverable": recoverable,
            "updated_at": now_local(),
        }
    )
    if persist_session and repository is not None:
        repository.save(updated)
    return updated
