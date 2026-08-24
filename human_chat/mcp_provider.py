"""把远程 MCP 工具适配为 HumanChat 的统一工具注册项。

LangChain MCP Adapter 原生提供异步工具，而当前 HumanChat 的 CLI 和 LangGraph
执行路径以同步调用为主。本模块同时承担两项边界职责：

* 发现 MCP 工具，并转换成带来源和安全策略的 ``RegisteredTool``；
* 用一个受控后台事件循环为同步调用提供异步执行入口。

Graph 只依赖 ``ToolRegistry``，因此不会感知一个工具来自本地代码还是 MCP Server。
"""

import asyncio
import threading
from collections.abc import Awaitable
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg

from human_chat.logging_config import get_logger
from human_chat.mcp_config import (
    McpConfig,
    McpPolicyConfig,
    McpServerConfig,
    resolve_mcp_connection,
)
from human_chat.tool_provider import RegisteredTool, ToolPolicy


logger = get_logger(__name__)


class McpDependencyError(RuntimeError):
    """启用了 MCP，但官方 LangChain MCP Adapter 依赖不可用。"""


class McpOperationTimeout(RuntimeError):
    """MCP 工具发现或调用超过了配置的最长等待时间。"""


class McpAsyncBridge:
    """在专用线程中运行长期存活的 asyncio 事件循环。

    ``MultiServerMCPClient`` 返回的工具以异步 coroutine 为真实执行入口。直接在
    每次同步调用中使用 ``asyncio.run`` 会反复创建事件循环，并可能破坏依赖事件
    循环生命周期的 MCP 传输资源。本桥接器为一个 ``open_tool_registry`` 生命周期
    只创建一个循环，所有发现和执行任务都提交到该循环。

    该对象拥有线程与事件循环，调用者必须执行 ``close``；项目通过
    ``open_tool_registry`` 的 ``finally`` 保证这一点。
    """

    def __init__(self):
        # 事件循环先在当前线程创建，再交给唯一后台线程运行。_ready 可以避免调用
        # 方在线程尚未进入 run_forever 时提交任务。
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run_event_loop,
            name="humanchat-mcp-event-loop",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

    def run(
        self,
        awaitable: Awaitable[Any],
        *,
        timeout: float,
        operation: str,
    ) -> Any:
        """在线程事件循环中执行 awaitable，并同步等待结果。

        ``operation`` 只用于构造可诊断的超时错误，不参与业务逻辑。超时时会取消
        后台 Future，避免已经失去调用方的远程任务继续无限运行。
        """

        if self._closed:
            # 调用者通常已经创建了 coroutine。若桥接器关闭后直接抛错而不关闭它，
            # Python 会产生 "coroutine was never awaited" 的资源警告。
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
            raise RuntimeError("MCP 异步桥接器已经关闭。")

        future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            future.cancel()
            raise McpOperationTimeout(
                f"{operation} 超时（{timeout:g} 秒）。"
            ) from exc

    def close(self) -> None:
        """幂等停止后台循环并等待线程退出。"""

        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            logger.warning("MCP event loop thread did not stop within 5 seconds")

    def _run_event_loop(self) -> None:
        """后台线程入口，并在停止时完整回收 asyncio 资源。"""

        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            # run_forever 停止并不等于任务已回收。显式取消任务、关闭异步生成器和
            # 默认执行器，可以避免进程退出时遗留连接、线程或 ResourceWarning。
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.run_until_complete(self._loop.shutdown_default_executor())
            self._loop.close()


class McpToolProvider:
    """从配置中的 MCP Server 加载工具注册项。

    本类通过 ``load_tools() -> list[RegisteredTool]`` 结构化满足 ``ToolProvider``
    Protocol，无需显式继承。它只负责“发现和适配”，事件循环资源由外层
    ``open_tool_registry`` 管理。

    默认情况下某个 Server 加载失败只会隔离该 Server，其他 MCP Server 和本地工具
    仍可使用；启用 ``fail_fast`` 后则把任何 Server 错误升级为启动失败。
    """

    def __init__(
        self,
        configuration: McpConfig,
        config_directory: Path,
        bridge: McpAsyncBridge,
        *,
        fail_fast: bool,
    ):
        self._configuration = configuration
        self._config_directory = config_directory
        self._bridge = bridge
        self._fail_fast = fail_fast
        self.loaded_server_count = 0
        self.loaded_tool_count = 0

    def load_tools(self) -> list[RegisteredTool]:
        """依次加载所有启用的 Server，并汇总为统一注册项。"""

        registrations = []
        enabled_servers = [
            (name, server)
            for name, server in self._configuration.servers.items()
            if server.enabled
        ]

        for server_name, server in enabled_servers:
            try:
                server_registrations = self._load_server_tools(
                    server_name,
                    server,
                )
            # 缺少进程级依赖并不是单个 Server 的临时故障，跳过所有 Server 也无法
            # 恢复，因此无论 fail_fast 如何都应直接暴露给启动入口。
            except McpDependencyError:
                raise
            except Exception as exc:
                if self._fail_fast:
                    raise RuntimeError(
                        f"MCP Server '{server_name}' 加载失败。"
                    ) from exc
                logger.exception(
                    "MCP server %s failed to load and will be skipped",
                    server_name,
                )
                continue

            registrations.extend(server_registrations)
            self.loaded_server_count += 1
            self.loaded_tool_count += len(server_registrations)

        if enabled_servers and not registrations:
            logger.warning("No MCP tools were loaded from the enabled servers")
        return registrations

    def _load_server_tools(
        self,
        server_name: str,
        server: McpServerConfig,
    ) -> list[RegisteredTool]:
        """完成一个 Server 的连接解析、工具发现、过滤和注册。"""

        # 配置中仍可能含 ${ENV_NAME} 和相对 cwd；直到真正连接前才解析真实值。
        connection = resolve_mcp_connection(
            server_name,
            server,
            self._config_directory,
        )
        # 官方 Adapter 的发现接口是异步的，通过共享桥接器同步等待，并用独立的
        # startup timeout 防止不可达 Server 阻塞整个应用启动。
        tools = self._bridge.run(
            self._discover_tools(server_name, connection),
            timeout=server.startup_timeout_seconds,
            operation=f"MCP Server '{server_name}' 工具发现",
        )
        # Adapter 为避免跨 Server 重名，在暴露给模型的名称前添加 Server 前缀；
        # 用户配置仍使用 Server 原始工具名，所以校验和策略匹配前需要去掉前缀。
        original_names = {
            self._original_tool_name(server_name, tool.name)
            for tool in tools
        }
        self._validate_tool_references(server_name, server, original_names)

        registrations = []
        for tool in tools:
            original_name = self._original_tool_name(server_name, tool.name)
            if server.include_tools and original_name not in server.include_tools:
                continue
            if original_name in server.exclude_tools:
                continue

            # ToolNode 可能沿同步路径调用工具，因此为只有 coroutine 的 MCP 工具
            # 补充同步入口；原 coroutine 保留，异步消费者仍可直接使用。
            self._add_sync_entrypoint(
                tool,
                server_name,
                original_name,
                server.tool_timeout_seconds,
            )
            registrations.append(
                RegisteredTool(
                    tool=tool,
                    source=f"mcp:{server_name}",
                    policy=self._resolve_policy(server, original_name, tool),
                )
            )
        logger.info(
            "MCP server %s discovered %s tools: %s registered, %s filtered",
            server_name,
            len(tools),
            len(registrations),
            len(tools) - len(registrations),
        )
        return registrations

    async def _discover_tools(
        self,
        server_name: str,
        connection: dict[str, Any],
    ):
        """使用官方 Adapter 连接一个 Server 并获取 LangChain 工具对象。"""

        # 延迟导入使 MCP 关闭时不要求加载 Adapter，也让缺少可选依赖时的错误只在
        # 用户实际启用 MCP 后出现。
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError as exc:
            raise McpDependencyError(
                "MCP 已启用，但未安装 langchain-mcp-adapters。"
            ) from exc

        client = MultiServerMCPClient(
            {server_name: connection},
            # 前缀从注册层根治不同 Server 返回同名工具的问题。
            tool_name_prefix=True,
            # Server 返回的业务错误作为 ToolMessage 交还给模型处理，不让一次远程
            # 工具失败直接终止整轮 Graph。
            handle_tool_errors=True,
        )
        return await client.get_tools(server_name=server_name)

    def _add_sync_entrypoint(
        self,
        tool,
        server_name: str,
        original_name: str,
        timeout: float,
    ) -> None:
        """为仅支持异步调用的 MCP 工具补充受超时保护的同步入口。

        ``runtime`` 是 LangChain 注入参数，不应出现在模型生成的参数 Schema 中；
        ``InjectedToolArg`` 正是用于表达这个边界。这里直接复用 Adapter 创建的
        coroutine，避免重新实现 MCP 请求协议。
        """

        coroutine = getattr(tool, "coroutine", None)
        if coroutine is None or getattr(tool, "func", None) is not None:
            return

        def invoke_sync(
            runtime: Annotated[object | None, InjectedToolArg()] = None,
            **arguments,
        ):
            return self._bridge.run(
                coroutine(runtime=runtime, **arguments),
                timeout=timeout,
                operation=f"MCP 工具 '{server_name}:{original_name}' 调用",
            )

        # StructuredTool 同时允许 func 和 coroutine。只填充缺失的 func，不覆盖
        # Adapter 未来可能提供的原生同步实现。
        tool.func = invoke_sync

    @staticmethod
    def _original_tool_name(server_name: str, prefixed_name: str) -> str:
        """从注册名称中移除当前 Server 前缀，并校验 Adapter 契约。"""

        prefix = f"{server_name}_"
        if not prefixed_name.startswith(prefix):
            raise RuntimeError(
                f"MCP 工具未包含预期的 Server 前缀：{prefixed_name}"
            )
        return prefixed_name[len(prefix):]

    @staticmethod
    def _validate_tool_references(
        server_name: str,
        server: McpServerConfig,
        available_names: set[str],
    ) -> None:
        """确保过滤和策略配置没有引用 Server 实际不存在的工具。

        这里选择启动时失败，而不是静默忽略拼写错误，否则本应受限制的工具可能
        因策略名称写错而落入默认权限。
        """

        configured_names = (
            set(server.include_tools)
            | set(server.exclude_tools)
            | set(server.tool_policies)
        )
        missing = configured_names - available_names
        if missing:
            names = ", ".join(sorted(missing))
            raise RuntimeError(
                f"MCP Server '{server_name}' 的配置引用了不存在的工具：{names}"
            )

    @staticmethod
    def _resolve_policy(
        server: McpServerConfig,
        original_name: str,
        tool,
    ) -> ToolPolicy:
        """把多层安全信息折叠成注册表使用的最终策略。

        优先级从高到低为：单工具覆盖、Server 默认策略、MCP 工具只读注解、系统
        保守默认值。未知工具默认视为可写，并由此默认要求人工确认。
        """

        annotation_read_only = _read_only_hint(tool)
        default = server.default_policy
        override = server.tool_policies.get(original_name, McpPolicyConfig())

        read_only = _first_defined(
            override.read_only,
            default.read_only,
            annotation_read_only,
            False,
        )
        requires_confirmation = _first_defined(
            override.requires_confirmation,
            default.requires_confirmation,
            not read_only,
        )
        return ToolPolicy(
            read_only=read_only,
            requires_confirmation=requires_confirmation,
        )


def _read_only_hint(tool) -> bool | None:
    """兼容读取 MCP 生态中常见的两种只读注解键名。"""

    metadata = getattr(tool, "metadata", None) or {}
    for key in ("readOnlyHint", "read_only_hint"):
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
    return None


def _first_defined(*values):
    """返回第一个非 None 值，用于实现策略的显式优先级。"""

    return next(value for value in values if value is not None)
