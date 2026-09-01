"""Readiness endpoint for the HumanChat API process."""

from fastapi import APIRouter

from human_chat import __version__
from human_chat.api.dependencies import HumanChatApplicationDependency
from human_chat.api.models import HealthFeatures, HealthResponse


router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def get_health(application: HumanChatApplicationDependency) -> HealthResponse:
    status = application.status()
    return HealthResponse(
        version=__version__,
        features=HealthFeatures(
            checkpoint_backend=status.checkpoint_backend,
            checkpoint_persistent=status.checkpoint_persistent,
            memory_backend=status.memory_backend,
            memory_persistent=status.memory_persistent,
            mcp_enabled=status.mcp_enabled,
            registered_tool_count=status.registered_tool_count,
            stt_enabled=status.stt_enabled,
            tts_enabled=status.tts_enabled,
        ),
    )
