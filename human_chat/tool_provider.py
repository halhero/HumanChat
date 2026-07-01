from dataclasses import dataclass
from typing import Any, Protocol

from human_chat.config import Settings
from human_chat.tools import get_project_tools


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    description: str
    source: str
    command: str = ""
    usage: str = ""
    read_only: bool = True
    requires_confirmation: bool = False


class ToolProvider(Protocol):
    def get_tools(self) -> list[Any]:
        """Return LangChain-compatible tools available to the agent."""

    def describe_tools(self) -> list[ToolMetadata]:
        """Return governance metadata for available tools."""

    def get_tool(self, name: str) -> Any:
        """Return one LangChain-compatible tool by name."""

    def get_metadata_by_command(self, command: str) -> ToolMetadata | None:
        """Return tool metadata for a CLI command."""

    def invoke_tool(self, name: str, arguments: dict | None = None) -> Any:
        """Invoke one tool through the unified provider."""


class LocalProjectToolProvider:
    source = "local_project"

    def get_tools(self) -> list[Any]:
        return get_project_tools()

    def get_tool(self, name: str) -> Any:
        tools = {tool.name: tool for tool in self.get_tools()}
        return tools[name]

    def describe_tools(self) -> list[ToolMetadata]:
        return [
            ToolMetadata(
                name="list_project_files",
                description="List readable files inside the HumanChat project.",
                source=self.source,
                command="/files",
                usage="/files",
            ),
            ToolMetadata(
                name="read_project_file",
                description="Read a UTF-8 text file inside the HumanChat project.",
                source=self.source,
                command="/read",
                usage="/read human_chat/graph.py",
            ),
            ToolMetadata(
                name="search_project_text",
                description="Search for exact text inside UTF-8 files in the HumanChat project.",
                source=self.source,
                command="/search",
                usage="/search memory",
            ),
        ]

    def get_metadata_by_command(self, command: str) -> ToolMetadata | None:
        metadata = {item.command: item for item in self.describe_tools()}
        return metadata.get(command)

    def invoke_tool(self, name: str, arguments: dict | None = None) -> Any:
        return self.get_tool(name).invoke(arguments or {})


class CompositeToolProvider:
    def __init__(self, providers: list[ToolProvider]):
        self.providers = providers
        self._validate_unique_tool_names()

    def get_tools(self) -> list[Any]:
        tools = []
        for provider in self.providers:
            tools.extend(provider.get_tools())
        return tools

    def describe_tools(self) -> list[ToolMetadata]:
        metadata = []
        for provider in self.providers:
            metadata.extend(provider.describe_tools())
        return metadata

    def get_tool(self, name: str) -> Any:
        for provider in self.providers:
            try:
                return provider.get_tool(name)
            except KeyError:
                continue
        raise KeyError(f"未知工具：{name}")

    def get_metadata_by_command(self, command: str) -> ToolMetadata | None:
        for provider in self.providers:
            metadata = provider.get_metadata_by_command(command)
            if metadata is not None:
                return metadata
        return None

    def invoke_tool(self, name: str, arguments: dict | None = None) -> Any:
        return self.get_tool(name).invoke(arguments or {})

    def _validate_unique_tool_names(self) -> None:
        names = [item.name for provider in self.providers for item in provider.describe_tools()]
        if len(names) != len(set(names)):
            raise ValueError("工具名称不能重复。")
        commands = [
            item.command
            for provider in self.providers
            for item in provider.describe_tools()
            if item.command
        ]
        if len(commands) != len(set(commands)):
            raise ValueError("工具命令不能重复。")


def create_tool_provider(settings: Settings | None = None) -> ToolProvider:
    _ = settings
    return CompositeToolProvider([LocalProjectToolProvider()])
