import json
from dataclasses import dataclass
from typing import Callable

from human_chat.input_provider import (
    AudioFileInputProvider,
    MicrophoneInputProvider,
    TextInputProvider,
)
from human_chat.logging_config import get_logger
from human_chat.runtime import ChatRuntime
from human_chat.tool_provider import RegisteredTool, ToolRegistry


logger = get_logger(__name__)


@dataclass
class CliContext:
    runtime: ChatRuntime
    input_provider: object
    debug_enabled: bool = False


@dataclass(frozen=True)
class CliCommand:
    name: str
    usage: str
    handler: Callable[[CliContext, str], None]


class CliCommandRegistry:
    def __init__(self, commands: list[CliCommand]):
        self._commands = {}
        for command in commands:
            if command.name in self._commands:
                raise ValueError(f"CLI 命令不能重复：{command.name}")
            self._commands[command.name] = command

    def dispatch(self, line: str, context: CliContext) -> bool:
        name, arguments = split_command_line(line)
        command = self._commands.get(name)
        if command is None:
            return False
        command.handler(context, arguments)
        return True


def create_cli_command_registry(tool_registry: ToolRegistry) -> CliCommandRegistry:
    commands = [
        CliCommand("/memory", "/memory [add 内容|delete 序号]", _handle_memory),
        CliCommand("/input", "/input text|audio-file|mic", _handle_input),
        CliCommand("/debug", "/debug on|off", _handle_debug),
        CliCommand("/tools", "/tools", _handle_tools),
    ]

    for registration in tool_registry.registrations():
        if registration.cli is None:
            continue
        commands.append(
            CliCommand(
                name=registration.cli.command,
                usage=registration.cli.usage,
                handler=_tool_handler(registration),
            )
        )
    return CliCommandRegistry(commands)


def split_command_line(line: str) -> tuple[str, str]:
    parts = line.strip().split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0], parts[1] if len(parts) > 1 else ""


def parse_tool_arguments(registration: RegisteredTool, value: str) -> dict:
    schema = registration.tool.args_schema
    fields = _schema_fields(schema)

    if not fields:
        if value.strip():
            raise ValueError(f"该工具不接受参数。用法：{registration.cli.usage}")
        return {}

    if len(fields) == 1:
        field_name = next(iter(fields))
        payload = {field_name: value.strip()} if value.strip() else {}
    else:
        if not value.strip():
            raise ValueError(
                f"多参数工具需要 JSON 对象。用法：{registration.cli.usage}"
            )
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("工具参数必须是有效 JSON 对象。") from exc
        if not isinstance(payload, dict):
            raise ValueError("工具参数必须是 JSON 对象。")

    try:
        if hasattr(schema, "model_validate"):
            validated = schema.model_validate(payload)
            return validated.model_dump()
        validated = schema.parse_obj(payload)
        return validated.dict()
    except Exception as exc:
        raise ValueError(f"工具参数无效：{exc}") from exc


def _schema_fields(schema) -> dict:
    if schema is None:
        return {}
    model_fields = getattr(schema, "model_fields", None)
    if model_fields is not None:
        return model_fields
    return getattr(schema, "__fields__", {})


def _handle_memory(context: CliContext, arguments: str) -> None:
    memory_service = context.runtime.memory_service
    if memory_service is None:
        print("长期记忆服务不可用。")
        return

    action, value = split_command_line(arguments)
    if not action:
        print(memory_service.format_for_prompt())
        return

    if action == "add":
        if not value:
            print("用法：/memory add 内容")
            return
        if memory_service.add(value):
            print("长期记忆已添加。")
        else:
            print("记忆为空或已存在，未添加。")
        return

    if action == "delete":
        try:
            index = int(value)
        except ValueError:
            print("用法：/memory delete 序号")
            return
        deleted = memory_service.delete(index)
        if deleted is None:
            print("未找到对应序号的长期记忆。")
        else:
            print(f"长期记忆已删除：{deleted}")
        return

    print("可用命令：/memory, /memory add ..., /memory delete ...")


def _handle_input(context: CliContext, arguments: str) -> None:
    mode = arguments.strip().lower()
    settings = context.runtime.settings
    if mode == "text":
        context.input_provider = TextInputProvider()
        print("已切换到文字输入。")
        return
    if mode == "audio-file":
        context.input_provider = AudioFileInputProvider(settings)
        print("已切换到音频文件输入。")
        return
    if mode == "mic":
        context.input_provider = MicrophoneInputProvider(settings)
        print("已切换到麦克风输入。")
        return
    print("用法：/input text|audio-file|mic")


def _handle_debug(context: CliContext, arguments: str) -> None:
    value = arguments.strip().lower()
    if not value:
        print(f"当前调试模式：{'on' if context.debug_enabled else 'off'}")
        print("用法：/debug on|off")
        return
    if value == "on":
        context.debug_enabled = True
        print("调试模式已开启。")
        return
    if value == "off":
        context.debug_enabled = False
        print("调试模式已关闭。")
        return
    print("用法：/debug on|off")


def _handle_tools(context: CliContext, arguments: str) -> None:
    if arguments.strip():
        print("用法：/tools")
        return
    print("可用工具命令：")
    for metadata in context.runtime.tool_registry.describe_tools():
        if not metadata.command:
            continue
        safety = "只读" if metadata.read_only else "可写"
        print(
            f"{metadata.command} - {metadata.description} "
            f"[{metadata.source}, {safety}]"
        )
        print(f"  用法：{metadata.usage}")


def _tool_handler(
    registration: RegisteredTool,
) -> Callable[[CliContext, str], None]:
    def handle(context: CliContext, arguments: str) -> None:
        try:
            parsed = parse_tool_arguments(registration, arguments)
            if registration.policy.requires_confirmation:
                choice = input(f"确认执行 {registration.name}？y/N：").strip().lower()
                if choice != "y":
                    print("已取消工具调用。")
                    return
            print(context.runtime.tool_registry.invoke_tool(registration.name, parsed))
        except Exception as exc:
            logger.exception("CLI tool command failed")
            print(f"工具执行失败：{exc}")

    return handle
