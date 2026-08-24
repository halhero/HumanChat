"""MCP Server 配置的读取、校验与运行时解析。

本模块刻意把 MCP 配置处理拆成两个阶段：

1. ``load_mcp_config`` 只负责读取 JSON 并校验声明是否合法；
2. ``resolve_mcp_connection`` 在真正连接 Server 前，才展开环境变量和路径。

这样配置模型中可以一直保留 ``${ENV_NAME}`` 占位符，而不必长期持有 Token、
密码等真实敏感值；同时也避免“读取配置”和“启动外部进程”发生隐式耦合。
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Server 名称会成为 MCP 工具名前缀，因此限制为稳定、适合工具名的字符集合。
SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
# 只支持明确的 ${NAME} 形式，避免实现 shell 插值时引入转义和命令执行语义。
ENV_REFERENCE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
HTTP_TRANSPORTS = {"http", "streamable_http", "streamable-http", "sse", "websocket"}
SUPPORTED_TRANSPORTS = {"stdio", *HTTP_TRANSPORTS}


class McpConfigError(ValueError):
    """MCP 配置无法安全读取或转换为运行时参数。"""


class McpPolicyConfig(BaseModel):
    """一个可部分填写的工具安全策略。

    两个字段都允许为 ``None``，表示当前层没有作出决定，继续使用 Server 默认
    策略、MCP 工具注解或系统保守默认值。这样同一个模型既能表示默认策略，也能
    表示仅覆盖一个字段的单工具策略。
    """

    model_config = ConfigDict(extra="forbid")

    read_only: bool | None = Field(
        default=None,
        description="工具是否只读取数据而不修改外部状态。",
    )
    requires_confirmation: bool | None = Field(
        default=None,
        description="Graph 执行工具前是否必须暂停并等待用户明确批准。",
    )


class McpServerConfig(BaseModel):
    """单个 MCP Server 的完整声明。

    ``connection`` 保留官方 MCP Adapter 接受的开放字典结构，因为不同 transport
    拥有不同参数；HumanChat 只校验连接所需的最小公共契约，其余字段原样转交给
    ``MultiServerMCPClient``。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="是否加载这个 Server；关闭后不连接也不发现它的工具。",
    )
    connection: dict[str, Any] = Field(
        description="传给 LangChain MCP Adapter 的 transport 及连接参数。",
    )
    include_tools: list[str] = Field(
        default_factory=list,
        description="允许注册的原始工具名白名单；空列表表示不启用白名单限制。",
    )
    exclude_tools: list[str] = Field(
        default_factory=list,
        description="明确禁止注册的原始工具名，优先于 include_tools。",
    )
    default_policy: McpPolicyConfig = Field(
        default_factory=McpPolicyConfig,
        description="这个 Server 中所有工具共用的默认安全策略。",
    )
    tool_policies: dict[str, McpPolicyConfig] = Field(
        default_factory=dict,
        description="按原始工具名覆盖 default_policy 的单工具安全策略。",
    )
    startup_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        description=(
            "连接 MCP Server 并完成工具发现允许等待的最长秒数；超时会取消发现任务。"
        ),
    )
    tool_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description=(
            "该 Server 中每一次具体工具调用允许等待的最长秒数；超时会取消调用任务。"
        ),
    )

    @field_validator("include_tools", "exclude_tools")
    @classmethod
    def validate_tool_names(cls, names: list[str]) -> list[str]:
        """尽早拒绝无法与 Server 返回名称稳定匹配的配置项。"""

        if any(not name or name != name.strip() for name in names):
            raise ValueError("工具名称不能为空或包含首尾空格。")
        return names

    @field_validator("tool_policies")
    @classmethod
    def validate_policy_names(
        cls,
        policies: dict[str, McpPolicyConfig],
    ) -> dict[str, McpPolicyConfig]:
        """保证单工具策略使用未经修剪、无歧义的原始工具名。"""

        if any(not name or name != name.strip() for name in policies):
            raise ValueError("tool_policies 的工具名称不能为空或包含首尾空格。")
        return policies

    @model_validator(mode="after")
    def validate_server(self) -> "McpServerConfig":
        """校验 transport 的最小连接契约和工具过滤规则的一致性。"""

        transport = str(self.connection.get("transport", "")).strip().lower()
        if transport not in SUPPORTED_TRANSPORTS:
            supported = ", ".join(sorted(SUPPORTED_TRANSPORTS))
            raise ValueError(f"connection.transport 必须是：{supported}")

        # stdio 由 HumanChat 启动本地子进程，必须知道命令和字符串参数；远程
        # transport 则至少需要 URL。更细的可选字段由官方 Adapter 继续校验。
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

        # include/exclude 同时配置同一工具会让最终行为取决于判断顺序，因此直接
        # 视为配置错误，而不是静默选择其中一条规则。
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
    """MCP 配置文件的顶层结构。

    ``version`` 为后续配置格式升级保留兼容边界；当前只接受版本 1，避免新版配置
    被旧代码误读。
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = Field(
        default=1,
        description="MCP 配置格式版本，当前只支持版本 1。",
    )
    servers: dict[str, McpServerConfig] = Field(
        default_factory=dict,
        description="以唯一 Server 名称为键的 MCP Server 配置集合。",
    )

    @model_validator(mode="after")
    def validate_server_names(self) -> "McpConfig":
        """保证 Server 名称可以安全参与工具名前缀生成。"""

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
    """读取并校验 MCP JSON 配置，但不连接 Server、也不展开秘密值。

    所有底层文件、JSON 和 Pydantic 错误在这里统一包装为 ``McpConfigError``，
    让运行时入口只需要处理一个稳定的配置异常类型。
    """

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
    """把声明式 Server 配置解析成 Adapter 可直接使用的连接字典。

    该函数返回一个新字典，不修改 ``McpServerConfig`` 中的原始配置。环境变量只
    在即将建立连接时展开；stdio 的相对 ``cwd`` 以配置文件目录为基准，而不是
    以当前 shell 的工作目录为基准，从而保证不同启动方式具有相同行为。
    """

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
    """递归展开连接结构中的环境变量，同时保持非字符串值的原始类型。"""

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
    """读取一个必需环境变量，并提供包含 Server 上下文的可定位错误。"""

    value = os.getenv(name)
    if value is None:
        raise McpConfigError(
            f"MCP Server '{server_name}' 引用了未定义的环境变量：{name}"
        )
    return value
