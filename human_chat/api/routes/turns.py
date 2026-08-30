"""Turn status, cancellation, and LangGraph interrupt-resume endpoints."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from human_chat.api.dependencies import ConversationServiceDependency
from human_chat.api.errors import ApiError
from human_chat.api.models import (
    CancelTurnResponse,
    ReviewDecisionRequest,
    TurnStatusResponse,
)
from human_chat.api.sse import conversation_stream_response
from human_chat.application import (
    ReviewDecisionError,
    TurnNotFoundError,
    TurnStateError,
    TurnStatus,
)


router = APIRouter(prefix="/turns", tags=["turns"])


@router.get("/{turn_id}", response_model=TurnStatusResponse)
async def get_turn(
    turn_id: str,
    service: ConversationServiceDependency,
) -> TurnStatusResponse:
    try:
        turn = await service.get_turn(turn_id)
    except TurnNotFoundError as exc:
        raise ApiError(404, "turn_not_found", str(exc)) from exc
    return TurnStatusResponse(
        id=turn.id,
        session_id=turn.session_id,
        status=turn.status,
        review=(
            turn.pending_review.public_payload
            if turn.pending_review is not None
            else None
        ),
    )


@router.post("/{turn_id}/decision")
async def submit_decision(
    turn_id: str,
    payload: ReviewDecisionRequest,
    service: ConversationServiceDependency,
) -> StreamingResponse:
    try:
        stream = await service.resume_turn(
            turn_id,
            decision=payload.decision,
            selected_item_ids=payload.selected_item_ids,
        )
    except TurnNotFoundError as exc:
        raise ApiError(404, "turn_not_found", str(exc)) from exc
    except (TurnStateError, ReviewDecisionError) as exc:
        raise ApiError(409, "invalid_turn_decision", str(exc)) from exc
    return conversation_stream_response(service, stream)


@router.post("/{turn_id}/cancel", response_model=CancelTurnResponse)
async def cancel_turn(
    turn_id: str,
    service: ConversationServiceDependency,
) -> CancelTurnResponse:
    try:
        turn_status = await service.cancel_turn(turn_id)
    except TurnNotFoundError as exc:
        raise ApiError(404, "turn_not_found", str(exc)) from exc
    except TurnStateError as exc:
        raise ApiError(409, "turn_not_cancellable", str(exc)) from exc
    if turn_status not in {TurnStatus.CANCELLING, TurnStatus.CANCELLED}:
        raise ApiError(409, "turn_not_cancellable", "该对话无法取消。")
    return CancelTurnResponse(id=turn_id, status=turn_status)
