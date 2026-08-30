"""Session-oriented conversation orchestration for interactive clients."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import AsyncIterator, Literal
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphDrained
from langgraph.runtime import RunControl

from human_chat.application.conversation_models import (
    STREAM_END,
    ConversationEvent,
    ConversationMessage,
    ConversationStream,
    ConversationTurn,
    PhaseOutcome,
    SessionBusyError,
    SessionNotFoundError,
    TurnNotFoundError,
    TurnStateError,
    TurnStatus,
)
from human_chat.application.conversation_protocol import (
    build_resume_value,
    create_pending_review,
    message_text,
    progress_event,
)
from human_chat.graph_interrupts import extract_interrupt_payloads
from human_chat.logging_config import get_logger
from human_chat.runtime import ChatApplication
from human_chat.session_models import SessionRecord, now_local


logger = get_logger(__name__)


class ConversationService:
    """Coordinate sessions, Graph runs, SSE events, cancellation, and review resumes.

    LangGraph remains the owner of conversational state and interrupt persistence. This
    service owns only process-local transport concerns: active turn exclusivity, safe
    public events, and mapping an HTTP decision back to ``Command(resume=...)``.
    """

    def __init__(
        self,
        application: ChatApplication,
        *,
        retained_turns: int = 100,
    ) -> None:
        self._application = application
        self._retained_turns = max(retained_turns, 10)
        self._turns: OrderedDict[str, ConversationTurn] = OrderedDict()
        self._active_sessions: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def create_session(self) -> SessionRecord:
        return self._application.session_repository.create()

    def list_sessions(self, limit: int = 20) -> list[SessionRecord]:
        return self._application.session_repository.list_recent(limit=limit)

    def get_session(self, session_id: str) -> SessionRecord:
        try:
            return self._application.session_repository.load(session_id)
        except (FileNotFoundError, ValueError) as exc:
            raise SessionNotFoundError("会话不存在。") from exc

    def get_messages(self, session_id: str) -> list[ConversationMessage]:
        session = self.get_session(session_id)
        runtime = self._application.create_runtime(session)
        snapshot = runtime.app.get_state(runtime.graph_config)
        messages = snapshot.values.get("messages", []) if snapshot.values else []
        public_messages = []
        for index, message in enumerate(messages):
            if isinstance(message, HumanMessage):
                role = "user"
            elif isinstance(message, AIMessage):
                role = "assistant"
            else:
                continue
            content = message_text(message)
            if content:
                public_messages.append(
                    ConversationMessage(
                        id=getattr(message, "id", None)
                        or f"{session.id}-{index}",
                        role=role,
                        content=content,
                    )
                )
        return public_messages

    async def start_turn(
        self,
        session_id: str,
        question: str,
    ) -> ConversationStream:
        session = self.get_session(session_id)
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("问题不能为空。")

        async with self._lock:
            if session_id in self._active_sessions:
                raise SessionBusyError("该会话已有一轮对话正在进行。")

            runtime = self._application.create_runtime(session)
            turn = ConversationTurn(
                id=uuid4().hex,
                session_id=session_id,
                runtime=runtime,
            )
            self._remember_turn(turn)
            self._active_sessions[session_id] = turn.id
            return self._start_phase(
                turn,
                graph_input=normalized_question,
                is_resume=False,
            )

    async def resume_turn(
        self,
        turn_id: str,
        *,
        decision: Literal["approve", "reject"],
        selected_item_ids: list[str] | None = None,
    ) -> ConversationStream:
        async with self._lock:
            turn = self._require_turn(turn_id)
            if turn.status != TurnStatus.AWAITING_REVIEW or turn.pending_review is None:
                raise TurnStateError("该对话当前不在等待确认。")

            resume_value = build_resume_value(
                turn.pending_review,
                decision=decision,
                selected_item_ids=selected_item_ids or [],
            )
            turn.status = TurnStatus.RUNNING
            turn.pending_review = None
            turn.updated_at = now_local()
            return self._start_phase(
                turn,
                graph_input=resume_value,
                is_resume=True,
            )

    async def cancel_turn(self, turn_id: str) -> TurnStatus:
        async with self._lock:
            turn = self._require_turn(turn_id)
            if turn.status == TurnStatus.AWAITING_REVIEW:
                turn.status = TurnStatus.CANCELLED
                turn.pending_review = None
                turn.updated_at = now_local()
                self._release_session(turn)
                return turn.status
            if turn.status == TurnStatus.RUNNING:
                turn.status = TurnStatus.CANCELLING
                turn.updated_at = now_local()
                if turn.control is not None:
                    turn.control.request_drain("user_cancelled")
                return turn.status
            if turn.status == TurnStatus.CANCELLING:
                return turn.status
            raise TurnStateError("该对话已经结束，无法取消。")

    async def get_turn(self, turn_id: str) -> ConversationTurn:
        async with self._lock:
            return self._require_turn(turn_id)

    async def iter_events(
        self,
        stream: ConversationStream,
    ) -> AsyncIterator[ConversationEvent]:
        while True:
            try:
                item = await asyncio.wait_for(stream.queue.get(), timeout=15)
            except TimeoutError:
                yield ConversationEvent(
                    type="heartbeat",
                    data={"time": now_local().isoformat()},
                )
                continue
            if item is STREAM_END:
                return
            yield item

    async def disconnect(self, turn_id: str) -> None:
        """Stop work when the only SSE consumer disappears mid-phase."""

        try:
            turn = await self.get_turn(turn_id)
        except TurnNotFoundError:
            return
        if turn.status in {TurnStatus.RUNNING, TurnStatus.CANCELLING}:
            try:
                await self.cancel_turn(turn_id)
            except TurnStateError:
                # The worker may finish between the status read and cancellation.
                return

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = []
            for turn in self._turns.values():
                if turn.status in {TurnStatus.RUNNING, TurnStatus.CANCELLING}:
                    if turn.control is not None:
                        turn.control.request_drain("application_shutdown")
                    if turn.task is not None:
                        tasks.append(turn.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _start_phase(
        self,
        turn: ConversationTurn,
        *,
        graph_input: str | dict,
        is_resume: bool,
    ) -> ConversationStream:
        queue: asyncio.Queue = asyncio.Queue()
        control = RunControl()
        turn.control = control
        turn.task = asyncio.create_task(
            self._run_phase(
                turn,
                queue,
                graph_input=graph_input,
                is_resume=is_resume,
                control=control,
            ),
            name=f"human-chat-turn-{turn.id}",
        )
        return ConversationStream(turn_id=turn.id, queue=queue)

    async def _run_phase(
        self,
        turn: ConversationTurn,
        queue: asyncio.Queue,
        *,
        graph_input: str | dict,
        is_resume: bool,
        control: RunControl,
    ) -> None:
        queue.put_nowait(
            ConversationEvent(
                type="turn.started" if not is_resume else "turn.resumed",
                data={"turn_id": turn.id, "session_id": turn.session_id},
            )
        )
        loop = asyncio.get_running_loop()

        try:
            outcome = await asyncio.to_thread(
                self._execute_phase,
                turn,
                graph_input,
                is_resume,
                control,
                loop,
                queue,
            )
        except GraphDrained:
            await self._finish_cancelled(turn, queue)
        except Exception:
            logger.exception("Conversation turn %s failed", turn.id)
            await self._finish_failed(turn, queue)
        else:
            async with self._lock:
                if turn.status == TurnStatus.CANCELLING:
                    turn.status = TurnStatus.CANCELLED
                    self._release_session(turn)
                    queue.put_nowait(
                        ConversationEvent(
                            type="turn.cancelled",
                            data={"turn_id": turn.id},
                        )
                    )
                elif outcome.review is not None:
                    turn.status = TurnStatus.AWAITING_REVIEW
                    turn.pending_review = outcome.review
                    queue.put_nowait(
                        ConversationEvent(
                            type="review.required",
                            data={
                                "turn_id": turn.id,
                                **outcome.review.public_payload,
                            },
                        )
                    )
                else:
                    turn.status = TurnStatus.COMPLETED
                    self._release_session(turn)
                    queue.put_nowait(
                        ConversationEvent(
                            type="turn.completed",
                            data={"turn_id": turn.id},
                        )
                    )
                turn.updated_at = now_local()
        finally:
            turn.control = None
            queue.put_nowait(STREAM_END)

    def _execute_phase(
        self,
        turn: ConversationTurn,
        graph_input: str | dict,
        is_resume: bool,
        control: RunControl,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue,
    ) -> PhaseOutcome:
        emit = lambda event: loop.call_soon_threadsafe(queue.put_nowait, event)
        stream = (
            turn.runtime.resume_stream(graph_input, control=control)
            if is_resume
            else turn.runtime.stream(graph_input, control=control)
        )
        review = None
        announced_stages: set[str] = set()

        for mode, data in stream:
            if mode != "updates" or not isinstance(data, dict):
                continue
            if control.drain_requested:
                continue
            payloads = extract_interrupt_payloads(data)
            if payloads:
                review = create_pending_review(payloads[0])
                continue
            for node_name, update in data.items():
                if not isinstance(update, dict):
                    continue
                stage_event = progress_event(node_name, announced_stages)
                if stage_event is not None:
                    emit(stage_event)
                if node_name == "finalize_reply" and update.get("assistant_text"):
                    emit(
                        ConversationEvent(
                            type="message.completed",
                            data={
                                "message": {
                                    "id": uuid4().hex,
                                    "role": "assistant",
                                    "content": update["assistant_text"],
                                }
                            },
                        )
                    )
                saved_count = update.get("memory_saved_count", 0)
                if saved_count:
                    emit(
                        ConversationEvent(
                            type="memory.saved",
                            data={"count": saved_count},
                        )
                    )
        return PhaseOutcome(review=review)

    async def _finish_cancelled(
        self,
        turn: ConversationTurn,
        queue: asyncio.Queue,
    ) -> None:
        async with self._lock:
            turn.status = TurnStatus.CANCELLED
            turn.updated_at = now_local()
            self._release_session(turn)
            queue.put_nowait(
                ConversationEvent(
                    type="turn.cancelled",
                    data={"turn_id": turn.id},
                )
            )

    async def _finish_failed(
        self,
        turn: ConversationTurn,
        queue: asyncio.Queue,
    ) -> None:
        async with self._lock:
            turn.status = TurnStatus.FAILED
            turn.updated_at = now_local()
            self._release_session(turn)
            queue.put_nowait(
                ConversationEvent(
                    type="turn.failed",
                    data={
                        "turn_id": turn.id,
                        "message": "本轮对话未能完成，请稍后重试。",
                    },
                )
            )

    def _remember_turn(self, turn: ConversationTurn) -> None:
        self._turns[turn.id] = turn
        self._turns.move_to_end(turn.id)
        while len(self._turns) > self._retained_turns:
            oldest_id, oldest = next(iter(self._turns.items()))
            if oldest.status in {
                TurnStatus.RUNNING,
                TurnStatus.CANCELLING,
                TurnStatus.AWAITING_REVIEW,
            }:
                break
            self._turns.pop(oldest_id)

    def _require_turn(self, turn_id: str) -> ConversationTurn:
        try:
            return self._turns[turn_id]
        except KeyError as exc:
            raise TurnNotFoundError("对话任务不存在或已经过期。") from exc

    def _release_session(self, turn: ConversationTurn) -> None:
        if self._active_sessions.get(turn.session_id) == turn.id:
            self._active_sessions.pop(turn.session_id, None)
