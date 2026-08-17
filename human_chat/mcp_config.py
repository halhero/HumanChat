import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ENV_REFERENCE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
HTTP_TRANSPORTS = {"http", "streamable_http", "streamable-http", "sse", "websocket"}
SUPPORTED_TRANSPORTS = {"stdio", *HTTP_TRANSPORTS}


class McpConfigError(ValueError):
    """Raised when MCP configuration cannot be loaded safely."""


class McpPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_only: bool | None = None
    requires_confirmation: bool | None = None


class McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    connection: dict[str, Any]
    include_tools: list[str] = Field(default_factory=list)
    exclude_tools: list[str] = Field(default_factory=list)
    default_policy: McpPolicyConfig = Field(default_factory=McpPolicyConfig)
    tool_policies: dict[str, McpPolicyConfig] = Field(default_factory=dict)
    startup_timeout_seconds: float = Field(default=15.0, gt=0)
    tool_timeout_seconds: float = Field(default=60.0, gt=0)

    @field_validator("include_tools", "exclude_tools")
    @classmethod
    def validate_tool_names(cls, names: list[str]) -> list[str]:
        if any(not name or name != name.strip() for name in names):
            raise ValueError("工具名称不能为空或包含首尾空格。")
        return names

    @field_validator("tool_policies")
    @classmethod
    def validate_policy_names(
        cls,
        policies: dict[str, McpPolicyConfig],
    ) -> dict[str, McpPolicyConfig]:
        if any(not name or name != name.strip() for name in policies):
            raise ValueError("tool_policies 的工具名称不能为空或包含首尾空格。")
        return policies

    @model_validator(mode="after")
    def validate_server(self) -> "McpServerConfig":
        transport = str(self.connection.get("transport", "")).strip().lower()
        if transport not in SUPPORTED_TRANSPORTS:
            supported = ", ".join(sorted(SUPPORTED_TRANSPORTS))
            raise ValueError(f"connection.transport 必须是：{supported}")

        if transport == "stdio":
            command = self.connection.get("command")
            args = self.connection.get("args")
            if not isinstance(command, str) or not command.strip():
                raise ValueError("stdio connection 必须提供非空 command。")
            if not isinstance(args, list) or not all(
                isinstance(item, str) for item in args
            ):
                raise ValueError("stdio connection.args 必须是字符串数组。")
        else:
            url = self.connection.get("url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError(f"{transport} connection 必须提供非空 url。")

        include = set(self.include_tools)
        exclude = set(self.exclude_tools)
        if len(include) != len(self.include_tools):
            raise ValueError("include_tools 不能包含重复名称。")
        if len(exclude) != len(self.exclude_tools):
            raise ValueError("exclude_tools 不能包含重复名称。")
        overlap = include & exclude
        if overlap:
            names = ", ".join(sorted(overlap))
            raise ValueError(f"工具不能同时出现在 include_tools 和 exclude_tools：{names}")
        return self


class McpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    servers: dict[str, McpServerConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_server_names(self) -> "McpConfig":
        invalid = [
            name for name in self.servers if not SERVER_NAME_PATTERN.fullmatch(name)
        ]
        if invalid:
            names = ", ".join(sorted(invalid))
            raise ValueError(
                "MCP Server 名称必须以字母开头，且只能包含字母、数字、下划线和连字符："
                f"{names}"
            )
        return self


def load_mcp_config(path: Path) -> McpConfig:
    if not path.exists():
        raise McpConfigError(f"MCP 配置文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise McpConfigError(
            f"MCP 配置不是有效 JSON：{path}（第 {exc.lineno} 行）"
        ) from exc
    except OSError as exc:
        raise McpConfigError(f"无法读取 MCP 配置文件：{path}") from exc

    try:
        return McpConfig.model_validate(data)
    except ValueError as exc:
        raise McpConfigError(f"MCP 配置校验失败：{exc}") from exc


def resolve_mcp_connection(
    server_name: str,
    server: McpServerConfig,
    config_directory: Path,
) -> dict[str, Any]:
    connection = _expand_environment(server.connection, server_name)
    transport = str(connection["transport"]).strip().lower()
    connection["transport"] = transport
    if transport == "stdio" and connection.get("cwd"):
        cwd = Path(str(connection["cwd"])).expanduser()
        if not cwd.is_absolute():
            cwd = config_directory / cwd
        connection["cwd"] = str(cwd.resolve())
    return connection


def _expand_environment(value: Any, server_name: str) -> Any:
    if isinstance(value, str):
        return ENV_REFERENCE_PATTERN.sub(
            lambda match: _environment_value(match.group(1), server_name),
            value,
        )
    if isinstance(value, list):
        return [_expand_environment(item, server_name) for item in value]
    if isinstance(value, dict):
        return {
            key: _expand_environment(item, server_name)
            for key, item in value.items()
        }
    return value


def _environment_value(name: str, server_name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise McpConfigError(
            f"MCP Server '{server_name}' 引用了未定义的环境变量：{name}"
        )
    return value
