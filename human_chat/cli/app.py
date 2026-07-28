from human_chat.cli.commands import (
    CliContext,
    create_cli_command_registry,
)
from human_chat.cli.debug import print_debug_summary
from human_chat.cli.interrupts import handle_graph_interrupts
from human_chat.config import Settings, load_settings
from human_chat.input_provider import (
    AudioFileInputProvider,
    MicrophoneInputProvider,
    TextInputProvider,
)
from human_chat.logging_config import get_logger, setup_logging
from human_chat.runtime import ChatRuntime, open_chat_runtime
from human_chat.session_models import SessionRecord
from human_chat.session_repository import SessionRepository
from human_chat.storage import create_session_repository
from human_chat.tts import start_tts_service, stop_tts_service


EXIT_COMMANDS = {"exit", "quit", "q", "退出"}
logger = get_logger(__name__)


def run_once(question: str, settings: Settings | None = None):
    setup_logging()
    settings = settings or load_settings()
    tts_process = _start_optional_tts(settings)

    try:
        with open_chat_runtime(
            settings,
            persist_session=False,
            checkpoint_backend="memory",
        ) as runtime:
            return runtime.ask(question)
    finally:
        if tts_process is not None:
            stop_tts_service(tts_process)


def chat_loop(settings: Settings | None = None) -> None:
    setup_logging()
    settings = settings or load_settings()
    tts_process = _start_optional_tts(settings)

    try:
        session_repository = create_session_repository(settings)
        session = _choose_session(session_repository)
        with open_chat_runtime(
            settings,
            session,
            session_repository=session_repository,
        ) as runtime:
            if session.message_count > 0 and not runtime.session.recoverable:
                print("该会话缺少可恢复的 Checkpoint，将从空上下文继续。")
            context = CliContext(
                runtime=runtime,
                input_provider=_choose_input_provider(settings),
            )
            _run_chat_loop(context)
    finally:
        if tts_process is not None:
            stop_tts_service(tts_process)


def _start_optional_tts(settings: Settings):
    if not settings.tts_auto_start:
        return None
    try:
        return start_tts_service(settings)
    except Exception:
        logger.exception("Failed to auto-start TTS service")
        print("TTS自动启动失败，将继续进行文本聊天。")
        return None


def _choose_input_provider(settings: Settings):
    print("请选择输入模式：")
    print("1. 文字输入")
    print("2. 音频文件输入")
    print("3. 麦克风输入")
    choice = input("选择：").strip()
    if choice == "2":
        print("已启用音频文件输入。你仍可输入 /memory、/files、exit 等命令。")
        return AudioFileInputProvider(settings)
    if choice == "3":
        print("已启用麦克风输入。你仍可输入 /memory、/files、exit 等命令。")
        return MicrophoneInputProvider(settings)
    return TextInputProvider()


def _choose_session(repository: SessionRepository) -> SessionRecord:
    recent_sessions = repository.list_recent(limit=10)
    if not recent_sessions:
        return _create_new_session(repository)

    print("请选择会话：")
    print("1. 新建会话")
    print("2. 继续最近会话")
    print("3. 从最近会话列表选择")
    choice = input("选择：").strip()

    if choice == "2":
        session_id = recent_sessions[0].id
        print(f"继续最近会话：{session_id}")
        return repository.load(session_id)

    if choice == "3":
        _print_recent_sessions(recent_sessions)
        selected = input("输入会话序号或会话 ID：").strip()
        session_id = _resolve_session_id(selected, recent_sessions)
        if session_id:
            print(f"继续会话：{session_id}")
            return repository.load(session_id)
        print("未找到该会话，将创建新会话。")

    return _create_new_session(repository)


def _create_new_session(repository: SessionRepository) -> SessionRecord:
    session = repository.create()
    print(f"已创建新会话：{session.id}")
    return session


def _print_recent_sessions(sessions: list[SessionRecord]) -> None:
    print("最近会话：")
    for index, session in enumerate(sessions, start=1):
        print(
            f"{index}. {session.id} "
            f"updated={session.updated_at.isoformat()} "
            f"messages={session.message_count}"
        )


def _resolve_session_id(
    value: str,
    sessions: list[SessionRecord],
) -> str | None:
    if value.isdigit():
        index = int(value) - 1
        if 0 <= index < len(sessions):
            return sessions[index].id
    for session in sessions:
        if session.id == value:
            return session.id
    return None


def _run_chat_loop(context: CliContext) -> None:
    runtime = context.runtime
    commands = create_cli_command_registry(runtime.tool_registry)
    print(f"HumanChat 已启动，会话：{runtime.session.id}")
    print("输入 exit / quit / q / 退出 可结束。")

    while True:
        try:
            question = context.input_provider.read_question()
        except Exception:
            logger.exception("Failed to read user input")
            print("读取输入失败，请检查音频文件或输入配置。")
            continue

        if not question:
            continue
        if question.lower() in EXIT_COMMANDS:
            print("HumanChat 已退出。")
            break
        if commands.dispatch(question, context):
            continue

        try:
            result = runtime.ask(question)
        except Exception:
            logger.exception("Chat turn failed")
            print("本轮对话失败，请检查模型配置、网络或服务状态。")
            continue

        _print_turn_result(runtime, result, context.debug_enabled)


def _print_turn_result(
    runtime: ChatRuntime,
    result: dict,
    debug_enabled: bool,
) -> None:
    if result.get("assistant_text"):
        print(f"助手：{result['assistant_text']}")
    if result.get("tts_error"):
        print(f"语音生成失败：{result['tts_error']}")

    resume_result = handle_graph_interrupts(runtime, result)
    if resume_result is not None:
        result = {**result, **resume_result}
        if result.get("memory_saved_count"):
            print(f"已保存 {result['memory_saved_count']} 条长期记忆。")

    if debug_enabled:
        print_debug_summary(result)
