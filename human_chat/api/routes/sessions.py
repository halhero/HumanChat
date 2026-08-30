"""Session discovery, creation, history, and turn-start endpoints."""

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse

from human_chat.api.dependencies import ConversationServiceDependency
from human_chat.api.errors import ApiError
from human_chat.api.models import (
    ChatMessageResponse,
    SessionDetailResponse,
    SessionListResponse,
    SessionSummary,
    StartTurnRequest,
)
from human_chat.api.sse import conversation_stream_response
from human_chat.application import (
    SessionBusyError,
    SessionNotFoundError,
)
from human_chat.session_models import SessionRecord


router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
def list_sessions(
    service: ConversationServiceDependency,
    limit: int = Query(default=20, ge=1, le=100),
) -> SessionListResponse:
    return SessionListResponse(
        items=[_session_summary(item) for item in service.list_sessions(limit)]
    )


@router.post(
    "",
    response_model=SessionSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_session(service: ConversationServiceDependency) -> SessionSummary:
    return _session_summary(service.create_session())


@router.get("/{session_id}", response_model=SessionDetailResponse)
def get_session(
    session_id: str,
    service: ConversationServiceDependency,
) -> SessionDetailResponse:
    try:
        session = service.get_session(session_id)
        messages = service.get_messages(session_id)
    except SessionNotFoundError as exc:
        raise ApiError(404, "session_not_found", str(exc)) from exc
    return SessionDetailResponse(
        session=_session_summary(session),
        messages=[
            ChatMessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
            )
            for message in messages
        ],
    )


@router.post("/{session_id}/turns")
async def start_turn(
    session_id: str,
    payload: StartTurnRequest,
    service: ConversationServiceDependency,
) -> StreamingResponse:
    try:
        stream = await service.start_turn(session_id, payload.question)
    except SessionNotFoundError as exc:
        raise ApiError(404, "session_not_found", str(exc)) from exc
    except SessionBusyError as exc:
        raise ApiError(409, "session_busy", str(exc)) from exc

    return conversation_stream_response(service, stream)


def _session_summary(session: SessionRecord) -> SessionSummary:
    return SessionSummary(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=session.message_count,
        recoverable=session.recoverable,
    )
