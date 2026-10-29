"""Process-scoped HumanChat application and managed resource composition."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import RunControl
from langgraph.types import Command

from human_chat.character import load_character
from human_chat.checkpointing import CheckpointerResource, open_checkpointer
from human_chat.config import Settings
from human_chat.graph import build_graph
from human_chat.llm import create_chat_model
from human_chat.logging_config import get_logger
from human_chat.memory.context import MemoryContextBuilder
from human_chat.memory.formation import MemoryFormation
from human_chat.memory.jobs import MemoryJobs, MemoryWorker
from human_chat.memory.request import recent_history
from human_chat.memory.resources import (
    MemoryResource,
    memory_sidecar_path,
    open_memory_resource,
)
from human_chat.memory.retrieval import MemoryRetriever
from human_chat.messages import SourceBatch, history_sources, new_source
from human_chat.session_models import SessionRecord, now_local
from human_chat.session_repository import SessionRepository
from human_chat.storage import create_session_repository
from human_chat.tool_provider import ToolRegistry
from human_chat.tool_resources import open_tool_registry
from human_chat.voice import (
    SpeechRecognitionError,
    SpeechSynthesisError,
    SynthesizedAudio,
    VoiceResource,
    open_voice_resource,
)

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
    stt_enabled: bool
    tts_enabled: bool


@dataclass(frozen=True)
class VoiceCapabilities:
    """Public voice capability state without exposing provider clients."""

    stt_enabled: bool
    tts_enabled: bool
    tts_available: bool
    tts_auto_start: bool


@dataclass(frozen=True)
class ChatMessage:
    """Framework-neutral message returned to interface adapters."""

    id: str
    role: Literal["user", "assistant"]
    content: str


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
        voice: VoiceResource,
    ) -> None:
        self._settings = settings
        self._graph = graph
        self._sessions = session_repository
        self._checkpoint = checkpoint
        self._memory = memory
        self._tool_registry = tool_registry
        self._voice = voice
        self._memory_worker = None
        self._memory_jobs = None

    def start_memory_worker(self, formation):
        path = memory_sidecar_path(self._settings.memory_path, "jobs")
        self._memory_jobs = MemoryJobs(path if self._checkpoint.persistent else None)
        self._memory_worker = MemoryWorker(self._memory_jobs, formation, self._memory.service,
                                           self._load_memory_source,
                                           enabled=self._settings.memory_extraction_enabled)
        # Reconcile completed checkpoint sources before starting the worker. This closes
        # the gap between a Graph commit and registration in the separate job database.
        for session in self._sessions.list_recent(limit=2**31 - 1):
            snapshot = self._graph.get_state(self._graph_config(session.thread_id))
            self._enqueue_memory_sources(session, snapshot, all_completed=True)
        self._memory_worker.start()

    def close_memory_worker(self):
        if self._memory_worker:
            self._memory_worker.close()
        if self._memory_jobs:
            self._memory_jobs.close()

    def _enqueue_memory_sources(self, session, snapshot, *, all_completed=False):
        if self._memory_jobs is None or not self._settings.memory_extraction_enabled or not snapshot.values:
            return
        messages = snapshot.values.get("messages", [])
        sources = history_sources(messages, user_id=self._settings.memory_user_id, session_id=session.id)
        checkpoint_id = snapshot.config["configurable"].get("checkpoint_id")
        if checkpoint_id is None:
            return
        for index, source in enumerate(sources[:-1]):
            if not all_completed and source.id != snapshot.values.get("input_source", {}).get("id"):
                continue
            if source.role == "user" and source.occurred_at and sources[index + 1].role == "assistant":
                self._memory_jobs.enqueue(user_id=source.user_id, thread_id=session.thread_id,
                    checkpoint_id=checkpoint_id, source_id=source.id, source_time=source.occurred_at)
        self._memory_worker.notify()

    def _load_memory_source(self, job):
        snapshot = self._graph.get_state({"configurable": {"thread_id": job["thread_id"], "checkpoint_id": job["checkpoint_id"]}})
        messages = snapshot.values.get("messages", [])
        sources = history_sources(messages, user_id=job["user_id"], session_id=job["thread_id"])
        position = next(i for i, source in enumerate(sources) if source.id == job["source_id"])
        previous = recent_history([source.to_message() for source in sources[:position]], self._settings.memory_history_tokens)
        context_ids = {message.id for message in previous}
        return SourceBatch(user_id=job["user_id"], new_messages=[sources[position]],
                           context_messages=[source for source in sources[:position] if source.id in context_ids])

    def status(self) -> ApplicationStatus:
        return ApplicationStatus(
            checkpoint_backend=self._checkpoint.backend,
            checkpoint_persistent=self._checkpoint.persistent,
            memory_backend=self._memory.backend,
            memory_persistent=self._memory.persistent,
            mcp_enabled=self._settings.mcp_enabled,
            registered_tool_count=len(self._tool_registry.registrations()),
            stt_enabled=self._voice.stt is not None,
            tts_enabled=self._voice.tts is not None,
        )

    def voice_capabilities(self) -> VoiceCapabilities:
        tts = self._voice.tts
        return VoiceCapabilities(
            stt_enabled=self._voice.stt is not None,
            tts_enabled=tts is not None,
            tts_available=tts.is_available() if tts is not None else False,
            tts_auto_start=self._settings.tts_auto_start,
        )

    def transcribe_audio(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: str,
    ) -> str:
        service = self._voice.stt
        if service is None:
            raise SpeechRecognitionError("语音识别服务未配置。")
        return service.transcribe(
            audio,
            filename=filename,
            content_type=content_type,
        )

    def synthesize_speech(self, text: str) -> SynthesizedAudio:
        service = self._voice.tts
        voice_config = self._voice.character.tts
        if service is None or voice_config is None:
            raise SpeechSynthesisError("当前角色未配置 TTS 服务。")
        return service.synthesize(text, voice_config)

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

    def get_session_with_messages(
        self,
        session_id: str,
    ) -> tuple[SessionRecord, list[ChatMessage]]:
        session = self.get_session(session_id)
        snapshot = self._graph.get_state(
            self._graph_config(session.thread_id)
        )
        messages = snapshot.values.get("messages", []) if snapshot.values else []
        public_messages = []
        for index, message in enumerate(messages):
            if isinstance(message, HumanMessage):
                role = "user"
            elif isinstance(message, AIMessage):
                role = "assistant"
            else:
                continue
            content = _message_text(message)
            if content:
                public_messages.append(
                    ChatMessage(
                        id=getattr(message, "id", None)
                        or f"{session.id}-{index}",
                        role=role,
                        content=content,
                    )
                )
        return session, public_messages

    def get_pending_review(self, session_id: str) -> dict | None:
        """Recover the review payload from the authoritative Graph checkpoint."""
        session = self.get_session(session_id)
        snapshot = self._graph.get_state(self._graph_config(session.thread_id))
        for pending in snapshot.interrupts:
            if isinstance(pending.value, dict):
                return pending.value
        return None

    def discard_pending_turn(self, session_id: str) -> None:
        """Finish a stopped conversation phase without running pending tools.

        The caller must have stopped the worker first. Updating as the terminal node
        clears pending Graph tasks while retaining the committed message history.
        """
        session = self.get_session(session_id)
        config = self._graph_config(session.thread_id)
        snapshot = self._graph.get_state(config)
        if snapshot.next:
            self._graph.update_state(
                config,
                {
                    "tool_messages": [],
                    "tool_review_request": None,
                    "tool_review_approved": None,
                    "memory_context": {},
                },
                as_node="finish_turn",
            )

    def stream_turn(
        self,
        session_id: str,
        question: str,
        *,
        control: RunControl,
    ) -> Iterator[tuple[str, Any]]:
        session = self.get_session(session_id)
        yield from self._stream_graph(
            session,
            {"question": question, "input_source": new_source(
                question, user_id=self._settings.memory_user_id, session_id=session.id,
            ).model_dump(mode="json")},
            control=control,
            question=question,
        )

    def resume_turn(
        self,
        session_id: str,
        value: dict,
        *,
        control: RunControl,
    ) -> Iterator[tuple[str, Any]]:
        session = self.get_session(session_id)
        yield from self._stream_graph(
            session,
            Command(resume=value),
            control=control,
        )

    @staticmethod
    def _graph_config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _stream_graph(
        self,
        session: SessionRecord,
        graph_input,
        *,
        control: RunControl,
        question: str | None = None,
    ) -> Iterator[tuple[str, Any]]:
        latest_state: dict = {}
        try:
            for mode, data in self._graph.stream(
                graph_input,
                config=self._graph_config(session.thread_id),
                stream_mode=["updates", "values"],
                durability="sync",
                control=control,
            ):
                if mode == "values" and isinstance(data, dict):
                    latest_state = data
                yield mode, data
            snapshot = self._graph.get_state(self._graph_config(session.thread_id))
            self._enqueue_memory_sources(session, snapshot)
        finally:
            if latest_state:
                self._save_session_state(
                    session,
                    latest_state,
                    question=question,
                )

    def _save_session_state(
        self,
        session: SessionRecord,
        result: dict,
        *,
        question: str | None = None,
    ) -> None:
        messages = result.get("messages", [])
        title = session.title
        if question and title == "新对话":
            title = _session_title(question)
        updated = session.model_copy(
            update={
                "title": title,
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
    character = load_character(settings.character_path)
    with (
        open_checkpointer(settings, backend=checkpoint_backend) as checkpoint,
        open_memory_resource(settings) as memory,
        open_tool_registry(settings) as tool_registry,
        open_voice_resource(settings, character) as voice,
    ):
        retriever = MemoryRetriever(memory.search, settings.memory_recall_limit)
        memory_llm = create_chat_model(settings, for_memory=True)
        graph = build_graph(
            settings,
            checkpointer=checkpoint.saver,
            memory_service=memory.service,
            tool_registry=tool_registry,
            character=character,
            context_builder=MemoryContextBuilder(retriever, memory_llm, settings),
        )
        application = HumanChatApplication(
            settings=settings,
            graph=graph,
            session_repository=repository,
            checkpoint=checkpoint,
            memory=memory,
            tool_registry=tool_registry,
            voice=voice,
        )
        try:
            if settings.memory_extraction_enabled:
                formation = MemoryFormation(memory.service, memory_llm, settings, retriever)
                application.start_memory_worker(formation)
            yield application
        finally:
            application.close_memory_worker()


def _message_text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content).strip()

    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("text") is not None:
            parts.append(str(block["text"]))
        elif getattr(block, "text", None) is not None:
            parts.append(str(block.text))
    return "".join(parts).strip()


def _session_title(question: str, max_length: int = 48) -> str:
    normalized = " ".join(question.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length].rstrip()}..."
