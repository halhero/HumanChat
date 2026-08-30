"""高风险工具调用的人工确认模型与敏感参数脱敏。

该模块不执行工具，只把模型生成的原始 tool call 转换为适合展示给用户的审批快照。
批准后 Graph 仍使用原始 tool call 执行，因此脱敏不会破坏真实参数；审批界面和调试
事件则永远只接触脱敏副本。
"""

import re
from typing import Any

from pydantic import BaseModel, Field

from human_chat.tool_provider import ToolRegistry


# 匹配的是参数“键”而不是值。先把空格、点号、连字符等分隔形式归一化，再判断
# 常见凭据词，可覆盖 api-key、api_key、auth.token 等常用命名方式。
SENSITIVE_KEY_PATTERN = re.compile(
    r"(^|_)(authorization|cookie|credential|password|secret|token|api_key|access_key)($|_)",
    re.IGNORECASE,
)


class ToolReviewCall(BaseModel):
    """单次待审批调用的只读展示快照。"""

    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    source: str
    read_only: bool
    requires_confirmation: bool


class ToolReviewRequest(BaseModel):
    """一次模型回复中所有工具调用组成的审批批次。"""

    calls: list[ToolReviewCall] = Field(default_factory=list)


class ToolReviewDecision(BaseModel):
    """用户对整个调用批次的决定；默认拒绝遵循 fail-closed 原则。"""

    approved: bool = False


def create_tool_review_request(
    tool_calls: list,
    registry: ToolRegistry,
) -> ToolReviewRequest:
    """根据注册表元数据构建脱敏后的审批请求。

    来源和安全属性以注册表为准，不信任模型在 tool call 中自行提供的描述；参数在
    进入可展示模型前递归脱敏，防止 API、日志或 checkpoint 泄露凭据。
    """

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
    """把 Graph resume 数据规范化为强类型决定，缺失值按拒绝处理。"""

    if data is None:
        return ToolReviewDecision()
    if isinstance(data, ToolReviewDecision):
        return data
    return ToolReviewDecision(**data)


def parse_tool_review_request(
    data: dict | ToolReviewRequest | None,
) -> ToolReviewRequest:
    """兼容 checkpoint 中的字典和进程内 Pydantic 模型。"""

    if data is None:
        return ToolReviewRequest()
    if isinstance(data, ToolReviewRequest):
        return data
    return ToolReviewRequest(**data)


def tool_calls_require_confirmation(
    tool_calls: list,
    registry: ToolRegistry,
) -> bool:
    """只要批次中有一个调用需要确认，就审批整个批次。

    这避免模型在一次回复中混合只读和写入调用时，让写入调用绕过确认。工具是否
    需要确认完全由注册策略决定，而不是由模型声明。
    """

    for tool_call in tool_calls:
        name = _tool_call_value(tool_call, "name", "unknown_tool")
        if registry.get_registration(name).policy.requires_confirmation:
            return True
    return False


def redact_tool_arguments(value):
    """递归复制参数结构，并替换名称疑似凭据的字段值。"""

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
    """把不同参数键命名风格转换成敏感词正则可识别的形式。"""

    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_")


def _tool_call_value(tool_call, key: str, default):
    """兼容 LangChain 使用的字典式和对象式 tool call 表示。"""

    if isinstance(tool_call, dict):
        return tool_call.get(key, default)
    return getattr(tool_call, key, default)
