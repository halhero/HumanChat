from dataclasses import dataclass
from typing import Any, Protocol

from human_chat.tools import get_project_tools


@dataclass(frozen=True)
class ToolPolicy:
    read_only: bool = True
    requires_confirmation: bool = False


@dataclass(frozen=True)
class RegisteredTool:
    tool: Any
    source: str
    policy: ToolPolicy = ToolPolicy()

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
    read_only: bool = True
    requires_confirmation: bool = False

    @classmethod
    def from_registration(cls, registration: RegisteredTool) -> "ToolMetadata":
        return cls(
            name=registration.name,
            description=registration.description,
            source=registration.source,
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
            ),
            RegisteredTool(
                tool=tools["read_project_file"],
                source=self.source,
            ),
            RegisteredTool(
                tool=tools["search_project_text"],
                source=self.source,
            ),
        ]


class ToolRegistry:
    def __init__(self, registrations: list[RegisteredTool]):
        self._registrations = list(registrations)
        self._by_name = self._index_by_name(self._registrations)

    @classmethod
    def from_providers(cls, providers: list[ToolProvider]) -> "ToolRegistry":
        registrations = []
        for provider in providers:
            registrations.extend(provider.load_tools())
        return cls(registrations)

    def get_tools(self) -> list[Any]:
        return [registration.tool for registration in self._registrations]

    def registrations(self) -> tuple[RegisteredTool, ...]:
        return tuple(self._registrations)

    def describe_tools(self) -> list[ToolMetadata]:
        return [
            ToolMetadata.from_registration(registration)
            for registration in self._registrations
        ]

    def get_tool(self, name: str) -> Any:
        return self.get_registration(name).tool

    def get_registration(self, name: str) -> RegisteredTool:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise KeyError(f"未知工具：{name}") from exc

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

def create_tool_registry(
    providers: list[ToolProvider] | None = None,
) -> ToolRegistry:
    return ToolRegistry.from_providers(
        providers or [LocalProjectToolProvider()]
    )
