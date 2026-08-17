from contextlib import contextmanager
from typing import Iterator

from human_chat.config import Settings
from human_chat.logging_config import get_logger
from human_chat.mcp_config import load_mcp_config
from human_chat.mcp_provider import McpAsyncBridge, McpToolProvider
from human_chat.tool_provider import (
    LocalProjectToolProvider,
    ToolRegistry,
    create_tool_registry,
)


logger = get_logger(__name__)


@contextmanager
def open_tool_registry(settings: Settings) -> Iterator[ToolRegistry]:
    providers = [LocalProjectToolProvider()]
    if not settings.mcp_enabled:
        yield create_tool_registry(providers)
        return

    configuration = load_mcp_config(settings.mcp_config_path)
    enabled_server_count = sum(
        1 for server in configuration.servers.values() if server.enabled
    )
    if enabled_server_count == 0:
        logger.warning("MCP is enabled but no MCP servers are enabled")
        yield create_tool_registry(providers)
        return

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
        yield registry
    finally:
        bridge.close()
