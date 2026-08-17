import re
from typing import Any

from pydantic import BaseModel, Field

from human_chat.tool_provider import ToolRegistry


SENSITIVE_KEY_PATTERN = re.compile(
    r"(^|_)(authorization|cookie|credential|password|secret|token|api_key|access_key)($|_)",
    re.IGNORECASE,
)


class ToolReviewCall(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    source: str
    read_only: bool
    requires_confirmation: bool


class ToolReviewRequest(BaseModel):
    calls: list[ToolReviewCall] = Field(default_factory=list)


class ToolReviewDecision(BaseModel):
    approved: bool = False


def create_tool_review_request(
    tool_calls: list,
    registry: ToolRegistry,
) -> ToolReviewRequest:
    calls = []
    for tool_call in tool_calls:
        name = _tool_call_value(tool_call, "name", "unknown_tool")
        arguments = _tool_call_value(tool_call, "args", {})
        call_id = _tool_call_value(tool_call, "id", "")
        registration = registry.get_registration(name)
        calls.append(
            ToolReviewCall(
                call_id=str(call_id),
                name=name,
                arguments=redact_tool_arguments(arguments),
                source=registration.source,
                read_only=registration.policy.read_only,
                requires_confirmation=registration.policy.requires_confirmation,
            )
        )
    return ToolReviewRequest(calls=calls)


def parse_tool_review_decision(
    data: dict | ToolReviewDecision | None,
) -> ToolReviewDecision:
    if data is None:
        return ToolReviewDecision()
    if isinstance(data, ToolReviewDecision):
        return data
    return ToolReviewDecision(**data)


def parse_tool_review_request(
    data: dict | ToolReviewRequest | None,
) -> ToolReviewRequest:
    if data is None:
        return ToolReviewRequest()
    if isinstance(data, ToolReviewRequest):
        return data
    return ToolReviewRequest(**data)


def tool_calls_require_confirmation(
    tool_calls: list,
    registry: ToolRegistry,
) -> bool:
    for tool_call in tool_calls:
        name = _tool_call_value(tool_call, "name", "unknown_tool")
        if registry.get_registration(name).policy.requires_confirmation:
            return True
    return False


def redact_tool_arguments(value):
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if SENSITIVE_KEY_PATTERN.search(_normalize_key(str(key)))
                else redact_tool_arguments(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_tool_arguments(item) for item in value]
    return value


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_")


def _tool_call_value(tool_call, key: str, default):
    if isinstance(tool_call, dict):
        return tool_call.get(key, default)
    return getattr(tool_call, key, default)
