from dataclasses import dataclass
from typing import Any, Protocol

from human_chat.config import Settings
from human_chat.tools import get_project_tools


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    description: str
    source: str
    read_only: bool = True
    requires_confirmation: bool = False


class ToolProvider(Protocol):
    def get_tools(self) -> list[Any]:
        """Return LangChain-compatible tools available to the agent."""

    def describe_tools(self) -> list[ToolMetadata]:
        """Return governance metadata for available tools."""


class LocalProjectToolProvider:
    source = "local_project"

    def get_tools(self) -> list[Any]:
        return get_project_tools()

    def describe_tools(self) -> list[ToolMetadata]:
        return [
            ToolMetadata(
                name="list_project_files",
                description="List readable files inside the HumanChat project.",
                source=self.source,
            ),
            ToolMetadata(
                name="read_project_file",
                description="Read a UTF-8 text file inside the HumanChat project.",
                source=self.source,
            ),
            ToolMetadata(
                name="search_project_text",
                description="Search for exact text inside UTF-8 files in the HumanChat project.",
                source=self.source,
            ),
        ]


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

    def _validate_unique_tool_names(self) -> None:
        names = [item.name for provider in self.providers for item in provider.describe_tools()]
        if len(names) != len(set(names)):
            raise ValueError("工具名称不能重复。")


def create_tool_provider(settings: Settings | None = None) -> ToolProvider:
    _ = settings
    return CompositeToolProvider([LocalProjectToolProvider()])
