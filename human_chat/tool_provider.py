from dataclasses import dataclass
from typing import Any, Protocol

from human_chat.tools import get_project_tools


@dataclass(frozen=True)
class ToolPolicy:
    read_only: bool = True
    requires_confirmation: bool = False


@dataclass(frozen=True)
class CliCommandSpec:
    command: str
    usage: str


@dataclass(frozen=True)
class RegisteredTool:
    tool: Any
    source: str
    policy: ToolPolicy = ToolPolicy()
    cli: CliCommandSpec | None = None

    @property
    def name(self) -> str:
        return self.tool.name

    @property
    def description(self) -> str:
        return self.tool.description


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    description: str
    source: str
    command: str = ""
    usage: str = ""
    read_only: bool = True
    requires_confirmation: bool = False

    @classmethod
    def from_registration(cls, registration: RegisteredTool) -> "ToolMetadata":
        command = registration.cli.command if registration.cli else ""
        usage = registration.cli.usage if registration.cli else ""
        return cls(
            name=registration.name,
            description=registration.description,
            source=registration.source,
            command=command,
            usage=usage,
            read_only=registration.policy.read_only,
            requires_confirmation=registration.policy.requires_confirmation,
        )


class ToolProvider(Protocol):
    def load_tools(self) -> list[RegisteredTool]:
        """Load tool registrations supplied by this provider."""


class LocalProjectToolProvider:
    source = "local_project"

    def load_tools(self) -> list[RegisteredTool]:
        tools = {tool.name: tool for tool in get_project_tools()}
        return [
            RegisteredTool(
                tool=tools["list_project_files"],
                source=self.source,
                cli=CliCommandSpec(command="/files", usage="/files"),
            ),
            RegisteredTool(
                tool=tools["read_project_file"],
                source=self.source,
                cli=CliCommandSpec(
                    command="/read",
                    usage="/read human_chat/graph.py",
                ),
            ),
            RegisteredTool(
                tool=tools["search_project_text"],
                source=self.source,
                cli=CliCommandSpec(command="/search", usage="/search memory"),
            ),
        ]


class ToolRegistry:
    def __init__(self, registrations: list[RegisteredTool]):
        self._registrations = list(registrations)
        self._by_name = self._index_by_name(self._registrations)
        self._by_command = self._index_by_command(self._registrations)

    @classmethod
    def from_providers(cls, providers: list[ToolProvider]) -> "ToolRegistry":
        registrations = []
        for provider in providers:
            registrations.extend(provider.load_tools())
        return cls(registrations)

    def get_tools(self) -> list[Any]:
        return [registration.tool for registration in self._registrations]

    def describe_tools(self) -> list[ToolMetadata]:
        return [
            ToolMetadata.from_registration(registration)
            for registration in self._registrations
        ]

    def get_tool(self, name: str) -> Any:
        try:
            return self._by_name[name].tool
        except KeyError as exc:
            raise KeyError(f"未知工具：{name}") from exc

    def get_registration_by_command(
        self,
        command: str,
    ) -> RegisteredTool | None:
        return self._by_command.get(command)

    def get_metadata_by_command(self, command: str) -> ToolMetadata | None:
        registration = self.get_registration_by_command(command)
        if registration is None:
            return None
        return ToolMetadata.from_registration(registration)

    def invoke_tool(self, name: str, arguments: dict | None = None) -> Any:
        return self.get_tool(name).invoke(arguments or {})

    @staticmethod
    def _index_by_name(
        registrations: list[RegisteredTool],
    ) -> dict[str, RegisteredTool]:
        indexed = {}
        for registration in registrations:
            if registration.name in indexed:
                raise ValueError(f"工具名称不能重复：{registration.name}")
            indexed[registration.name] = registration
        return indexed

    @staticmethod
    def _index_by_command(
        registrations: list[RegisteredTool],
    ) -> dict[str, RegisteredTool]:
        indexed = {}
        for registration in registrations:
            if registration.cli is None:
                continue
            command = registration.cli.command
            if command in indexed:
                raise ValueError(f"工具命令不能重复：{command}")
            indexed[command] = registration
        return indexed


def create_tool_registry(
    providers: list[ToolProvider] | None = None,
) -> ToolRegistry:
    return ToolRegistry.from_providers(
        providers or [LocalProjectToolProvider()]
    )
