import pytest
from langchain_core.tools import tool

from human_chat.cli.commands import (
    CliContext,
    create_cli_command_registry,
    parse_tool_arguments,
)
from human_chat.config import Settings
from human_chat.memory_service import LongTermMemoryService
from human_chat.session_models import SessionRecord
from human_chat.tool_provider import (
    CliCommandSpec,
    RegisteredTool,
    ToolRegistry,
)


@tool("echo_tool")
def echo_tool(value: str) -> str:
    """Echo one value."""
    return value


@tool("no_arg_tool")
def no_arg_tool() -> str:
    """Return a fixed value."""
    return "fixed"


class InMemoryRepository:
    def __init__(self):
        self.items = {}

    def list_items(self, namespace):
        return list(self.items.values())

    def get_item(self, namespace, item_id):
        return self.items.get(item_id)

    def upsert_item(self, namespace, item):
        self.items[item.id] = item

    def delete_item(self, namespace, item_id):
        return self.items.pop(item_id, None) is not None


class FakeRuntime:
    def __init__(self, registry):
        self.settings = Settings()
        self.session = SessionRecord.create()
        self.tool_registry = registry
        self.memory_service = LongTermMemoryService(
            InMemoryRepository(),
            ("users", "test", "memory"),
        )


def create_registry():
    return ToolRegistry(
        [
            RegisteredTool(
                tool=echo_tool,
                source="test",
                cli=CliCommandSpec(command="/echo", usage="/echo value"),
            ),
            RegisteredTool(
                tool=no_arg_tool,
                source="test",
                cli=CliCommandSpec(command="/fixed", usage="/fixed"),
            ),
        ]
    )


def test_command_registry_matches_exact_command_tokens(capsys):
    registry = create_registry()
    context = CliContext(runtime=FakeRuntime(registry), input_provider=object())
    commands = create_cli_command_registry(registry)

    assert commands.dispatch("/memory add 用户喜欢测试", context)
    assert not commands.dispatch("/memoryx add 不应匹配", context)
    assert context.runtime.memory_service.load().items[0].text == "用户喜欢测试"
    assert "长期记忆已添加" in capsys.readouterr().out


def test_tool_commands_are_generated_from_registry(capsys):
    registry = create_registry()
    context = CliContext(runtime=FakeRuntime(registry), input_provider=object())
    commands = create_cli_command_registry(registry)

    assert commands.dispatch("/echo hello world", context)
    assert commands.dispatch("/fixed", context)

    output = capsys.readouterr().out
    assert "hello world" in output
    assert "fixed" in output


def test_tool_argument_parser_uses_tool_schema():
    registration = create_registry().get_registration_by_command("/echo")

    assert parse_tool_arguments(registration, "hello") == {"value": "hello"}

    with pytest.raises(ValueError):
        parse_tool_arguments(registration, "")


def test_debug_command_updates_cli_context():
    registry = create_registry()
    context = CliContext(runtime=FakeRuntime(registry), input_provider=object())
    commands = create_cli_command_registry(registry)

    commands.dispatch("/debug on", context)

    assert context.debug_enabled
