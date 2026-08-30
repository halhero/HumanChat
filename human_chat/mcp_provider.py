"""把远程 MCP 工具适配为 HumanChat 的统一工具注册项。

LangChain MCP Adapter 原生提供异步工具，而当前 HumanChat 的 LangGraph ToolNode
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

# 批量发现内部已经对每个 Server 应用独立 startup timeout。这里的额外时间只用于
# 允许任务取消和传输资源清理完成，避免同步调用方比后台 loop 更早放弃整个批次。
DISCOVERY_BATCH_GRACE_SECONDS = 5.0


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

        参数：
            awaitable: 要提交到 MCP 后台事件循环的异步任务，例如工具发现或调用。
            timeout: 当前任务最多允许执行的秒数。调用者分别传入 Server 的启动
                超时或工具调用超时字段。
            operation: 面向日志和异常的操作名称，只用于说明“哪个操作超时”，
                不参与 MCP 请求。

        返回：
            异步任务完成后的真实结果。这个方法会阻塞当前同步线程，直到任务完成、
            抛出异常或达到 ``timeout``。

        异常：
            McpOperationTimeout: 达到超时时间后取消后台 Future，并提供操作上下文。
            RuntimeError: 桥接器已经关闭，不能再接收新任务。
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
        """保存 Provider 构建工具注册表所需的依赖。

        参数：
            configuration: 已通过 Pydantic 校验的完整 MCP 配置。
            config_directory: 配置文件所在目录，用于解析 stdio 的相对 ``cwd``。
            bridge: 由外层资源管理器持有的异步桥接器，本类只使用、不负责关闭。
            fail_fast: 任一 Server 失败时是否立即终止注册表构建；为 ``False`` 时
                记录错误并继续加载其他 Server。

        ``loaded_server_count`` 和 ``loaded_tool_count`` 是本次加载结果的可观测指标，
        供启动日志使用，不参与工具路由。
        """

        self._configuration = configuration
        self._config_directory = config_directory
        self._bridge = bridge
        self._fail_fast = fail_fast
        self.loaded_server_count = 0
        self.loaded_tool_count = 0

    def load_tools(self) -> list[RegisteredTool]:
        """并发发现所有启用的 Server，再按配置顺序生成工具注册项。

        网络连接和工具发现发生在后台事件循环中，可以并发等待；工具过滤、策略
        解析和注册仍在当前同步线程按配置顺序完成，因此最终工具顺序是确定的。
        """

        registrations: list[RegisteredTool] = []
        enabled_servers: list[tuple[str, McpServerConfig]] = [
            (name, server)
            for name, server in self._configuration.servers.items()
            if server.enabled
        ]
        if not enabled_servers:
            return registrations

        # 只跨线程提交一次批量协程。future.result() 会阻塞当前同步线程，但后台 loop
        # 已经同时调度多个 Server Task，因此网络和进程等待时间可以重叠。
        discoveries = self._bridge.run(
            self._discover_enabled_servers(enabled_servers),
            timeout=self._discovery_batch_timeout(enabled_servers),
            operation="MCP Server 批量工具发现",
        )

        # gather 按传入顺序返回结果，即使 Server 的实际完成顺序不同，也不会改变
        # ToolRegistry 中的注册顺序和冲突检测顺序。
        for (server_name, server), discovery in zip(
            enabled_servers,
            discoveries,
            strict=True,
        ):
            if isinstance(discovery, BaseException):
                self._handle_server_failure(server_name, discovery)
                continue

            try:
                server_registrations = self._register_server_tools(
                    server_name,
                    server,
                    discovery,
                )
            except Exception as exc:
                self._handle_server_failure(server_name, exc)
                continue

            registrations.extend(server_registrations)
            self.loaded_server_count += 1
            self.loaded_tool_count += len(server_registrations)

        if not registrations:
            logger.warning("No MCP tools were loaded from the enabled servers")
        return registrations

    async def _discover_enabled_servers(
        self,
        enabled_servers: list[tuple[str, McpServerConfig]],
    ) -> list:
        """创建具名 Task，并在当前 MCP 事件循环中受控并发发现工具。

        ``return_exceptions=True`` 仅用于非 fail-fast 模式，使单个 Server 的异常成为
        对应位置的结果并由同步层记录。fail-fast 模式让首个异常立即向上传播，同时
        在 ``finally`` 中取消仍未完成的 Server Task。
        """

        client_class = _load_mcp_client_class()
        concurrency = min(
            self._configuration.max_concurrent_server_discoveries,
            len(enabled_servers),
        )
        semaphore = asyncio.Semaphore(concurrency)
        tasks = [
            asyncio.create_task(
                self._discover_server_tools(
                    server_name,
                    server,
                    semaphore,
                    client_class,
                ),
                name=f"mcp-discovery:{server_name}",
            )
            for server_name, server in enabled_servers
        ]

        try:
            return await asyncio.gather(
                *tasks,
                return_exceptions=not self._fail_fast,
            )
        finally:
            # gather 在默认模式下遇到异常不会自动取消其他子任务。显式取消并等待
            # 可以实现真正的 fail-fast，也能在批量任务被外部取消时完整回收资源。
            unfinished = [task for task in tasks if not task.done()]
            for task in unfinished:
                task.cancel()
            if unfinished:
                await asyncio.gather(*unfinished, return_exceptions=True)

    async def _discover_server_tools(
        self,
        server_name: str,
        server: McpServerConfig,
        semaphore: asyncio.Semaphore,
        client_class,
    ) -> list:
        """在并发槽位内发现一个 Server，并应用它自己的启动超时。

        等待 semaphore 的时间不计入 Server 启动超时；只有获得槽位、真正开始连接
        后才启动 ``asyncio.wait_for`` 计时。这样并发上限不会无意中消耗排队 Server
        的连接预算。
        """

        try:
            # 环境变量和 cwd 在各自 Task 内解析，配置错误也能按 Server 隔离报告。
            connection = resolve_mcp_connection(
                server_name,
                server,
                self._config_directory,
            )
            async with semaphore:
                return await asyncio.wait_for(
                    self._discover_tools(
                        server_name,
                        connection,
                        client_class,
                    ),
                    timeout=server.startup_timeout_seconds,
                )
        except asyncio.TimeoutError as exc:
            raise McpOperationTimeout(
                f"MCP Server '{server_name}' 工具发现超时"
                f"（{server.startup_timeout_seconds:g} 秒）。"
            ) from exc
        except McpDependencyError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"MCP Server '{server_name}' 工具发现失败。"
            ) from exc

    def _register_server_tools(
        self,
        server_name: str,
        server: McpServerConfig,
        tools: list,
    ) -> list[RegisteredTool]:
        """把已经发现的一个 Server 工具转换为 HumanChat 注册项。

        此方法不执行网络 I/O。它在同步线程中应用 include/exclude、配置引用校验、
        同步调用入口和最终安全策略。
        """

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

            # tool_timeout_seconds 同样是配置字段。它会被同步入口捕获，之后这个
            # 工具每次被 ToolNode 调用时，都以该值限制最长执行时间。
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
        client_class,
    ):
        """使用官方 Adapter 连接一个 Server 并获取 LangChain 工具对象。"""

        client = client_class(
            {server_name: connection},
            # 前缀从注册层根治不同 Server 返回同名工具的问题。
            tool_name_prefix=True,
            # Server 返回的业务错误作为 ToolMessage 交还给模型处理，不让一次远程
            # 工具失败直接终止整轮 Graph。
            handle_tool_errors=True,
        )
        return await client.get_tools(server_name=server_name)

    def _handle_server_failure(
        self,
        server_name: str,
        error: BaseException,
    ) -> None:
        """根据 fail-fast 策略处理发现或注册阶段的单 Server 失败。"""

        # Adapter 缺失是进程级依赖问题，跳过某一个 Server 无法恢复，因此始终
        # 直接中止启动，不受 fail_fast 配置影响。
        if isinstance(error, McpDependencyError):
            raise error
        if self._fail_fast:
            raise RuntimeError(
                f"MCP Server '{server_name}' 加载失败。"
            ) from error
        logger.error(
            "MCP server %s failed to load and will be skipped",
            server_name,
            exc_info=(type(error), error, error.__traceback__),
        )

    @staticmethod
    def _discovery_batch_timeout(
        enabled_servers: list[tuple[str, McpServerConfig]],
    ) -> float:
        """计算同步桥接层的批量安全超时。

        每个 Task 已有独立 wait_for；这里使用所有启动超时之和作为保守上界，既能
        覆盖并发受限时的排队批次，也不会改变正常并发完成的实际等待时间。
        """

        return (
            sum(server.startup_timeout_seconds for _, server in enabled_servers)
            + DISCOVERY_BATCH_GRACE_SECONDS
        )

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
        coroutine，避免重新实现 MCP 请求协议。参数 ``timeout`` 来自当前 Server 的
        ``tool_timeout_seconds``，作用于之后的每一次工具执行，而不是工具发现阶段。
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


def _load_mcp_client_class():
    """仅在 MCP 实际启用时加载官方 Adapter Client 类型。"""

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        raise McpDependencyError(
            "MCP 已启用，但未安装 langchain-mcp-adapters。"
        ) from exc
    return MultiServerMCPClient
