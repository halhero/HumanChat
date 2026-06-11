from human_chat.config import Settings, load_settings
from human_chat.graph import build_graph
from human_chat.logging_config import get_logger, setup_logging
from human_chat.memory_store import (
    add_memory_item,
    delete_memory_item,
    format_memory_for_prompt,
    load_memory,
    save_memory,
)
from human_chat.session_store import (
    create_session,
    dicts_to_messages,
    list_sessions,
    load_session,
    messages_to_dicts,
    save_session,
)
from human_chat.tts import start_tts_service, stop_tts_service


EXIT_COMMANDS = {"exit", "quit", "q", "退出"}
MEMORY_COMMAND = "/memory"
logger = get_logger(__name__)


def run_once(question: str, settings: Settings | None = None):
    setup_logging()
    settings = settings or load_settings()
    tts_process = _start_optional_tts(settings)

    try:
        app = build_graph(settings)
        return app.invoke({"question": question, "messages": []})
    finally:
        if tts_process is not None:
            stop_tts_service(tts_process)


def chat_loop(settings: Settings | None = None) -> None:
    setup_logging()
    settings = settings or load_settings()
    tts_process = _start_optional_tts(settings)

    try:
        app = build_graph(settings)
        session = _choose_session(settings)
        _run_chat_loop(app, settings, session)
    finally:
        if tts_process is not None:
            stop_tts_service(tts_process)


def _start_optional_tts(settings: Settings):
    tts_process = None
    if settings.tts_auto_start:
        try:
            tts_process = start_tts_service(settings)
        except Exception:
            logger.exception("Failed to auto-start TTS service")
            print("TTS自动启动失败，将继续进行文本聊天。")
    return tts_process


def _choose_session(settings: Settings) -> dict:
    recent_sessions = list_sessions(settings, limit=10)

    if not recent_sessions:
        return _create_new_session(settings)

    print("请选择会话：")
    print("1. 新建会话")
    print("2. 继续最近会话")
    print("3. 从最近会话列表选择")

    choice = input("选择：").strip()

    if choice == "2":
        session_id = recent_sessions[0]["id"]
        session = load_session(settings, session_id)
        print(f"继续最近会话：{session_id}")
        return session

    if choice == "3":
        _print_recent_sessions(recent_sessions)
        selected = input("输入会话序号或会话 ID：").strip()
        session_id = _resolve_session_id(selected, recent_sessions)

        if session_id:
            session = load_session(settings, session_id)
            print(f"继续会话：{session_id}")
            return session

        print("未找到该会话，将创建新会话。")

    return _create_new_session(settings)


def _create_new_session(settings: Settings) -> dict:
    session = create_session()
    save_session(settings, session)
    print(f"已创建新会话：{session['id']}")
    return session


def _print_recent_sessions(sessions: list[dict]) -> None:
    print("最近会话：")

    for index, session in enumerate(sessions, start=1):
        print(
            f"{index}. {session['id']} "
            f"updated={session['updated_at']} "
            f"messages={session['message_count']}"
        )


def _resolve_session_id(value: str, sessions: list[dict]) -> str | None:
    if value.isdigit():
        index = int(value) - 1
        if 0 <= index < len(sessions):
            return sessions[index]["id"]

    for session in sessions:
        if session["id"] == value:
            return session["id"]

    return None


def _run_chat_loop(app, settings: Settings, session: dict) -> None:
    messages = dicts_to_messages(session.get("messages", []))
    print(f"HumanChat 已启动，会话：{session['id']}")
    print("输入 exit / quit / q / 退出 可结束。")

    while True:
        question = input("\n你：").strip()

        if not question:
            continue

        if question.lower() in EXIT_COMMANDS:
            print("HumanChat 已退出。")
            break

        if question.startswith(MEMORY_COMMAND):
            _handle_memory_command(settings, question)
            continue

        try:
            result = app.invoke(
                {
                    "question": question,
                    "messages": messages,
                }
            )
        except Exception:
            logger.exception("Chat turn failed")
            print("本轮对话失败，请检查模型配置、网络或服务状态。")
            continue

        messages = result.get("messages", messages)
        session["messages"] = messages_to_dicts(messages)
        save_session(settings, session)

        answer = result.get("tts_text", "")
        if answer:
            print(f"助手：{answer}")

        tts_error = result.get("tts_error", "")
        if tts_error:
            print(f"语音生成失败：{tts_error}")


def _handle_memory_command(settings: Settings, command: str) -> None:
    parts = command.split(maxsplit=3)
    memory = load_memory(settings.memory_path)

    if len(parts) == 1:
        print(format_memory_for_prompt(memory))
        return

    action = parts[1].lower()

    if action == "add":
        if len(parts) < 4:
            print("用法：/memory add preference|fact|note 内容")
            return
        category = parts[2]
        text = parts[3]
        try:
            added = add_memory_item(memory, category, text)
        except ValueError as exc:
            print(exc)
            return
        if not added:
            print("记忆为空或已存在，未添加。")
            return
        save_memory(settings.memory_path, memory)
        print("长期记忆已添加。")
        return

    if action == "delete":
        if len(parts) < 4:
            print("用法：/memory delete preference|fact|note 序号")
            return
        category = parts[2]
        try:
            index = int(parts[3])
            deleted = delete_memory_item(memory, category, index)
        except ValueError as exc:
            print(exc)
            return
        if deleted is None:
            print("未找到对应序号的长期记忆。")
            return
        save_memory(settings.memory_path, memory)
        print(f"长期记忆已删除：{deleted}")
        return

    print("可用命令：/memory, /memory add ..., /memory delete ...")
