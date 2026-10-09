import asyncio
import threading

import pytest
from langchain_core.tools import tool

from human_chat.mcp_provider import McpAsyncBridge
from human_chat.tool_provider import (
    RegisteredTool,
    ToolRegistry,
    create_tool_registry,
)


@tool("demo_tool")
def demo_tool(value: str) -> str:
    """Return a demo value."""
    return value


class CountingProvider:
    def __init__(self):
        self.load_count = 0

    def load_tools(self):
        self.load_count += 1
        return [
            RegisteredTool(
                tool=demo_tool,
                source="test",
            )
        ]


def test_registry_loads_provider_once_and_indexes_tools():
    provider = CountingProvider()

    registry = create_tool_registry([provider])

    assert provider.load_count == 1
    assert registry.get_tool("demo_tool") is demo_tool
    assert registry.invoke_tool("demo_tool", {"value": "ok"}) == "ok"


def test_metadata_is_derived_from_langchain_tool():
    registry = create_tool_registry([CountingProvider()])

    metadata = registry.describe_tools()[0]

    assert metadata.name == demo_tool.name
    assert metadata.description == demo_tool.description
    assert metadata.source == "test"
    assert metadata.read_only


def test_registry_rejects_duplicate_tool_names():
    registration = RegisteredTool(tool=demo_tool, source="test")

    with pytest.raises(ValueError, match="工具名称不能重复"):
        ToolRegistry([registration, registration])


def test_mcp_bridge_creates_and_closes_loop_in_its_own_thread(monkeypatch):
    new_event_loop = asyncio.new_event_loop
    creation_threads = []
    created_loops = []

    def record_event_loop():
        creation_threads.append(threading.current_thread().name)
        loop = new_event_loop()
        created_loops.append(loop)
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", record_event_loop)
    bridge = McpAsyncBridge()
    try:
        assert bridge.run(
            asyncio.sleep(0, result="ok"), timeout=2, operation="validation"
        ) == "ok"
    finally:
        bridge.close()
        bridge.close()

    assert creation_threads == ["humanchat-mcp-event-loop"]
    assert created_loops[0].is_closed()
    assert not bridge._thread.is_alive()


def test_mcp_bridge_reports_event_loop_startup_failure(monkeypatch):
    def fail_event_loop():
        raise OSError("isolated loop creation failure")

    monkeypatch.setattr(asyncio, "new_event_loop", fail_event_loop)
    with pytest.raises(RuntimeError) as failure:
        McpAsyncBridge()

    assert isinstance(failure.value.__cause__, OSError)
