"""Session collection and history endpoints."""

from fastapi import APIRouter, Query, status

from human_chat.api.dependencies import HumanChatApplicationDependency
from human_chat.api.errors import ApiError
from human_chat.api.models import (
    ChatMessageResponse,
    SessionDetailResponse,
    SessionListResponse,
    SessionSummary,
)
from human_chat.session_models import SessionRecord


router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
def list_sessions(
    application: HumanChatApplicationDependency,
    limit: int = Query(default=20, ge=1, le=100),
) -> SessionListResponse:
    return SessionListResponse(
        sessions=[
            _session_summary(session)
            for session in application.list_sessions(limit=limit)
        ]
    )


@router.post(
    "",
    response_model=SessionSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_session(
    application: HumanChatApplicationDependency,
) -> SessionSummary:
    return _session_summary(application.create_session())


@router.get("/{session_id}", response_model=SessionDetailResponse)
def get_session(
    session_id: str,
    application: HumanChatApplicationDependency,
) -> SessionDetailResponse:
    try:
        session, messages = application.get_session_with_messages(session_id)
    except (FileNotFoundError, ValueError) as exc:
        raise ApiError(404, "session_not_found", "会话不存在。") from exc
    return SessionDetailResponse(
        **_session_summary(session).model_dump(),
        messages=[
            ChatMessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
            )
            for message in messages
        ],
    )


def _session_summary(session: SessionRecord) -> SessionSummary:
    return SessionSummary(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=session.message_count,
    )
