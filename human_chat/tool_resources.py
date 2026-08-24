"""统一创建并管理本地工具与 MCP 工具注册表。"""

from contextlib import contextmanager
from typing import Iterator

from human_chat.config import Settings
from human_chat.logging_config import get_logger
from human_chat.mcp_config import load_mcp_config
from human_chat.mcp_provider import McpAsyncBridge, McpToolProvider
from human_chat.tool_provider import (
    LocalProjectToolProvider,
    ToolProvider,
    ToolRegistry,
    create_tool_registry,
)


logger = get_logger(__name__)


@contextmanager
def open_tool_registry(settings: Settings) -> Iterator[ToolRegistry]:
    """在一个明确生命周期内构建应用使用的完整工具注册表。

    本地 Provider 不持有外部资源，始终可用；MCP Provider 依赖后台事件循环，必须
    覆盖 Graph 的全部调用周期。因此本函数返回上下文管理器，而不是一个普通工厂
    函数，并在退出时统一关闭桥接器。
    """

    # 显式声明协议类型，允许列表同时容纳结构化满足 ToolProvider 的本地 Provider
    # 和 McpToolProvider，也避免静态类型检查器把它收窄成 LocalProjectToolProvider。
    providers: list[ToolProvider] = [LocalProjectToolProvider()]
    if not settings.mcp_enabled:
        # MCP 默认关闭时完全不读取配置、不加载 Adapter，也不会创建额外线程。
        yield create_tool_registry(providers)
        return

    configuration = load_mcp_config(settings.mcp_config_path)
    enabled_server_count = sum(
        1 for server in configuration.servers.values() if server.enabled
    )
    if enabled_server_count == 0:
        # 总开关开启但没有启用 Server 不属于致命错误，本地工具仍然可以工作。
        logger.warning("MCP is enabled but no MCP servers are enabled")
        yield create_tool_registry(providers)
        return

    # 一个注册表共享一个事件循环；工具发现和之后的每次调用都使用同一桥接器。
    bridge = McpAsyncBridge()
    try:
        mcp_provider = McpToolProvider(
            configuration,
            settings.mcp_config_path.parent,
            bridge,
            fail_fast=settings.mcp_fail_fast,
        )
        providers.append(mcp_provider)
        registry = create_tool_registry(providers)
        logger.info(
            "MCP registry ready: %s/%s servers, %s tools",
            mcp_provider.loaded_server_count,
            enabled_server_count,
            mcp_provider.loaded_tool_count,
        )
        # yield 期间 Graph 可能多次调用 MCP 工具，所以桥接器必须保持存活。
        yield registry
    finally:
        # 无论构图、聊天还是工具调用是否抛错，都保证线程和连接相关任务被回收。
        bridge.close()
