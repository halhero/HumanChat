"""Public response models shared by HumanChat API routes."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthFeatures(BaseModel):
    checkpoint_backend: str
    checkpoint_persistent: bool
    memory_backend: str
    mcp_enabled: bool
    registered_tool_count: int = Field(ge=0)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["HumanChat API"] = "HumanChat API"
    version: str
    features: HealthFeatures
