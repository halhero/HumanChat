"""Readiness endpoint for the HumanChat API process."""

from fastapi import APIRouter

from human_chat import __version__
from human_chat.api.dependencies import ChatApplicationDependency
from human_chat.api.models import HealthFeatures, HealthResponse


router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def get_health(application: ChatApplicationDependency) -> HealthResponse:
    return HealthResponse(
        version=__version__,
        features=HealthFeatures(
            checkpoint_backend=application.checkpoint.backend,
            checkpoint_persistent=application.checkpoint.persistent,
            memory_backend=application.memory.backend,
            mcp_enabled=application.settings.mcp_enabled,
            registered_tool_count=len(application.tool_registry.registrations()),
        ),
    )
