"""Coordinate active turns without exposing backend resources to FastAPI."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import AsyncIterator, Literal
from uuid import uuid4

from langgraph.errors import GraphDrained
from langgraph.runtime import RunControl

from human_chat.application import HumanChatApplication
from human_chat.conversation.models import (
    STREAM_END,
    ConversationEvent,
    ConversationStream,
    ConversationTurn,
    SessionBusyError,
    SessionNotFoundError,
    TurnNotFoundError,
    TurnSnapshot,
    TurnStateError,
    TurnStatus,
)
from human_chat.conversation.protocol import (
    build_resume_value,
    create_pending_review,
    extract_interrupt_payloads,
    progress_event,
)
from human_chat.logging_config import get_logger
from human_chat.session_models import now_local


logger = get_logger(__name__)


class ConversationService:
    """Manage HTTP turn state while LangGraph owns Agent execution state."""

    def __init__(
        self,
        application: HumanChatApplication,
        *,
        retained_turns: int = 100,
    ) -> None:
        self._application = application
        self._retained_turns = max(retained_turns, 10)
        self._turns: OrderedDict[str, ConversationTurn] = OrderedDict()
        self._active_sessions: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def start_turn(
        self,
        session_id: str,
        question: str,
    ) -> ConversationStream:
        try:
            await asyncio.to_thread(self._application.get_session, session_id)
        except (FileNotFoundError, ValueError) as exc:
            raise SessionNotFoundError("会话不存在。") from exc

        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("问题不能为空。")

        async with self._lock:
            if session_id in self._active_sessions:
                raise SessionBusyError("该会话已有一轮对话正在进行。")
            turn = ConversationTurn(
                id=uuid4().hex,
                session_id=session_id,
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
        selected_item_ids: list[str],
    ) -> ConversationStream:
        async with self._lock:
            turn = self._require_turn(turn_id)
            if turn.status != TurnStatus.AWAITING_REVIEW or turn.pending_review is None:
                raise TurnStateError("该对话当前不在等待确认。")
            resume_value = build_resume_value(
                turn.pending_review,
                decision=decision,
                selected_item_ids=selected_item_ids,
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

    async def get_turn(self, turn_id: str) -> TurnSnapshot:
        async with self._lock:
            turn = self._require_turn(turn_id)
            return TurnSnapshot(
                id=turn.id,
                session_id=turn.session_id,
                status=turn.status,
                review=(
                    turn.pending_review.public_payload
                    if turn.pending_review is not None
                    else None
                ),
            )

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
        try:
            snapshot = await self.get_turn(turn_id)
            if snapshot.status in {TurnStatus.RUNNING, TurnStatus.CANCELLING}:
                await self.cancel_turn(turn_id)
        except (TurnNotFoundError, TurnStateError):
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
                type="turn.resumed" if is_resume else "turn.started",
                data={"turn_id": turn.id, "session_id": turn.session_id},
            )
        )
        loop = asyncio.get_running_loop()
        try:
            review = await asyncio.to_thread(
                self._execute_phase,
                turn,
                queue,
                loop,
                graph_input,
                is_resume,
                control,
            )
        except GraphDrained:
            await self._finish_turn(turn, queue, TurnStatus.CANCELLED)
        except Exception:
            logger.exception("Conversation turn %s failed", turn.id)
            await self._finish_turn(turn, queue, TurnStatus.FAILED)
        else:
            async with self._lock:
                if turn.status == TurnStatus.CANCELLING:
                    turn.status = TurnStatus.CANCELLED
                    self._release_session(turn)
                    queue.put_nowait(
                        ConversationEvent("turn.cancelled", {"turn_id": turn.id})
                    )
                elif review is not None:
                    turn.status = TurnStatus.AWAITING_REVIEW
                    turn.pending_review = review
                    queue.put_nowait(
                        ConversationEvent(
                            "review.required",
                            {"turn_id": turn.id, **review.public_payload},
                        )
                    )
                else:
                    turn.status = TurnStatus.COMPLETED
                    self._release_session(turn)
                    queue.put_nowait(
                        ConversationEvent("turn.completed", {"turn_id": turn.id})
                    )
                turn.updated_at = now_local()
        finally:
            turn.control = None
            queue.put_nowait(STREAM_END)

    def _execute_phase(
        self,
        turn: ConversationTurn,
        queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
        graph_input: str | dict,
        is_resume: bool,
        control: RunControl,
    ):
        def emit(event: ConversationEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        stream = (
            self._application.resume_turn(
                turn.session_id,
                graph_input,
                control=control,
            )
            if is_resume
            else self._application.stream_turn(
                turn.session_id,
                graph_input,
                control=control,
            )
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
                progress = progress_event(node_name, announced_stages)
                if progress is not None:
                    emit(progress)
                if node_name == "finalize_reply" and update.get("assistant_text"):
                    emit(
                        ConversationEvent(
                            "message.completed",
                            {
                                "message": {
                                    "id": uuid4().hex,
                                    "role": "assistant",
                                    "content": update["assistant_text"],
                                }
                            },
                        )
                    )
                if update.get("memory_saved_count"):
                    emit(
                        ConversationEvent(
                            "memory.saved",
                            {"count": update["memory_saved_count"]},
                        )
                    )
        return review

    async def _finish_turn(
        self,
        turn: ConversationTurn,
        queue: asyncio.Queue,
        status: TurnStatus,
    ) -> None:
        async with self._lock:
            turn.status = status
            turn.updated_at = now_local()
            self._release_session(turn)
            if status == TurnStatus.CANCELLED:
                event = ConversationEvent("turn.cancelled", {"turn_id": turn.id})
            else:
                event = ConversationEvent(
                    "turn.failed",
                    {
                        "turn_id": turn.id,
                        "message": "本轮对话未能完成，请稍后重试。",
                    },
                )
            queue.put_nowait(event)

    def _remember_turn(self, turn: ConversationTurn) -> None:
        self._turns[turn.id] = turn
        self._turns.move_to_end(turn.id)
        for turn_id, candidate in list(self._turns.items()):
            if len(self._turns) <= self._retained_turns:
                break
            if candidate.status in {
                TurnStatus.COMPLETED,
                TurnStatus.CANCELLED,
                TurnStatus.FAILED,
            }:
                self._turns.pop(turn_id)

    def _require_turn(self, turn_id: str) -> ConversationTurn:
        try:
            return self._turns[turn_id]
        except KeyError as exc:
            raise TurnNotFoundError("对话任务不存在或已经过期。") from exc

    def _release_session(self, turn: ConversationTurn) -> None:
        if self._active_sessions.get(turn.session_id) == turn.id:
            self._active_sessions.pop(turn.session_id, None)
