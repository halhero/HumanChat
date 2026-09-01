"""Process-scoped HumanChat application and managed resource composition."""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import RunControl
from langgraph.types import Command

from human_chat.checkpointing import CheckpointerResource, open_checkpointer
from human_chat.character import load_character
from human_chat.config import Settings
from human_chat.graph import build_graph
from human_chat.logging_config import get_logger
from human_chat.memory_resources import MemoryResource, open_memory_resource
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
            {"question": question},
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
    with open_checkpointer(settings, backend=checkpoint_backend) as checkpoint:
        with open_memory_resource(settings) as memory:
            with open_tool_registry(settings) as tool_registry:
                with open_voice_resource(settings, character) as voice:
                    graph = build_graph(
                        settings,
                        checkpointer=checkpoint.saver,
                        memory_service=memory.service,
                        tool_registry=tool_registry,
                        character=character,
                    )
                    yield HumanChatApplication(
                        settings=settings,
                        graph=graph,
                        session_repository=repository,
                        checkpoint=checkpoint,
                        memory=memory,
                        tool_registry=tool_registry,
                        voice=voice,
                    )


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
