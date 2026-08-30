"""Streaming conversation, review and cancellation endpoints."""

from fastapi import APIRouter, Request

from human_chat.api.dependencies import ConversationServiceDependency
from human_chat.api.errors import ApiError
from human_chat.api.models import (
    CancelTurnResponse,
    ReviewDecisionRequest,
    StartTurnRequest,
    TurnStatusResponse,
)
from human_chat.api.sse import conversation_stream_response
from human_chat.conversation import (
    ReviewDecisionError,
    SessionBusyError,
    SessionNotFoundError,
    TurnNotFoundError,
    TurnStateError,
    TurnStatus,
)


router = APIRouter(tags=["turns"])


@router.post("/sessions/{session_id}/turns")
async def start_turn(
    session_id: str,
    payload: StartTurnRequest,
    request: Request,
    conversations: ConversationServiceDependency,
):
    try:
        stream = await conversations.start_turn(session_id, payload.message)
    except SessionNotFoundError as exc:
        raise ApiError(404, "session_not_found", str(exc)) from exc
    except SessionBusyError as exc:
        raise ApiError(409, "session_busy", str(exc)) from exc
    return conversation_stream_response(request, conversations, stream)


@router.get("/turns/{turn_id}", response_model=TurnStatusResponse)
async def get_turn(
    turn_id: str,
    conversations: ConversationServiceDependency,
) -> TurnStatusResponse:
    try:
        turn = await conversations.get_turn(turn_id)
    except TurnNotFoundError as exc:
        raise ApiError(404, "turn_not_found", str(exc)) from exc
    return TurnStatusResponse(
        id=turn.id,
        session_id=turn.session_id,
        status=turn.status,
        review=turn.review,
    )


@router.post("/turns/{turn_id}/decision")
async def decide_turn(
    turn_id: str,
    payload: ReviewDecisionRequest,
    request: Request,
    conversations: ConversationServiceDependency,
):
    try:
        stream = await conversations.resume_turn(
            turn_id,
            decision=payload.decision,
            selected_item_ids=payload.selected_item_ids,
        )
    except TurnNotFoundError as exc:
        raise ApiError(404, "turn_not_found", str(exc)) from exc
    except (TurnStateError, ReviewDecisionError) as exc:
        raise ApiError(409, "invalid_turn_decision", str(exc)) from exc
    return conversation_stream_response(request, conversations, stream)


@router.post("/turns/{turn_id}/cancel", response_model=CancelTurnResponse)
async def cancel_turn(
    turn_id: str,
    conversations: ConversationServiceDependency,
) -> CancelTurnResponse:
    try:
        turn_status = await conversations.cancel_turn(turn_id)
    except TurnNotFoundError as exc:
        raise ApiError(404, "turn_not_found", str(exc)) from exc
    except TurnStateError as exc:
        raise ApiError(409, "turn_already_finished", str(exc)) from exc
    if turn_status not in {TurnStatus.CANCELLING, TurnStatus.CANCELLED}:
        raise ApiError(409, "turn_not_cancellable", "该对话无法取消。")
    return CancelTurnResponse(id=turn_id, status=turn_status)
