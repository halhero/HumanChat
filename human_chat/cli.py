from human_chat.config import Settings, load_settings
from human_chat.graph import build_graph
from human_chat.logging_config import get_logger, setup_logging
from human_chat.tts import start_tts_service, stop_tts_service


EXIT_COMMANDS = {"exit", "quit", "q", "退出"}
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
        _run_chat_loop(app)
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


def _run_chat_loop(app) -> None:
    messages = []
    print("HumanChat 已启动，输入 exit / quit / q / 退出 可结束。")

    while True:
        question = input("\n你：").strip()

        if not question:
            continue

        if question.lower() in EXIT_COMMANDS:
            print("HumanChat 已退出。")
            break

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
        answer = result.get("tts_text", "")
        if answer:
            print(f"助手：{answer}")

        tts_error = result.get("tts_error", "")
        if tts_error:
            print(f"语音生成失败：{tts_error}")
