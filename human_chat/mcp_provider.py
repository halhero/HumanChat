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
    """Raised when the optional MCP adapter dependency is unavailable."""


class McpOperationTimeout(RuntimeError):
    """Raised when MCP discovery or execution exceeds its configured timeout."""


class McpAsyncBridge:
    def __init__(self):
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
        if self._closed:
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
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            logger.warning("MCP event loop thread did not stop within 5 seconds")

    def _run_event_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
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
        connection = resolve_mcp_connection(
            server_name,
            server,
            self._config_directory,
        )
        tools = self._bridge.run(
            self._discover_tools(server_name, connection),
            timeout=server.startup_timeout_seconds,
            operation=f"MCP Server '{server_name}' 工具发现",
        )
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
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError as exc:
            raise McpDependencyError(
                "MCP 已启用，但未安装 langchain-mcp-adapters。"
            ) from exc

        client = MultiServerMCPClient(
            {server_name: connection},
            tool_name_prefix=True,
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

        tool.func = invoke_sync

    @staticmethod
    def _original_tool_name(server_name: str, prefixed_name: str) -> str:
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
    metadata = getattr(tool, "metadata", None) or {}
    for key in ("readOnlyHint", "read_only_hint"):
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
    return None


def _first_defined(*values):
    return next(value for value in values if value is not None)
