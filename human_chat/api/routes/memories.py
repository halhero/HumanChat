"""Manual long-term memory management endpoints."""

from fastapi import APIRouter, Response, status

from human_chat.api.dependencies import HumanChatApplicationDependency
from human_chat.api.errors import ApiError
from human_chat.api.models import (
    CreateMemoryRequest,
    MemoryItemResponse,
    MemoryListResponse,
)
from human_chat.memory_models import MemoryItem


router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("", response_model=MemoryListResponse)
def list_memories(
    application: HumanChatApplicationDependency,
) -> MemoryListResponse:
    return MemoryListResponse(
        items=[_memory_response(item) for item in application.list_memories()]
    )


@router.post(
    "",
    response_model=MemoryItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_memory(
    payload: CreateMemoryRequest,
    application: HumanChatApplicationDependency,
) -> MemoryItemResponse:
    item = application.add_memory(payload.text)
    if item is None:
        raise ApiError(409, "memory_exists", "该条长期记忆已存在。")
    return _memory_response(item)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    memory_id: str,
    application: HumanChatApplicationDependency,
) -> Response:
    if len(memory_id) > 128 or application.delete_memory(memory_id) is None:
        raise ApiError(404, "memory_not_found", "长期记忆不存在。")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _memory_response(item: MemoryItem) -> MemoryItemResponse:
    return MemoryItemResponse(
        id=item.id,
        text=item.text,
        created_at=item.created_at,
        updated_at=item.updated_at,
        source=item.source,
        confidence=item.confidence,
    )
