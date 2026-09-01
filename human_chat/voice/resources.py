"""Process-scoped voice service composition and optional TTS process ownership."""

import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from human_chat.character import Character
from human_chat.config import Settings
from human_chat.logging_config import get_logger
from human_chat.voice.stt import OpenAICompatibleSttService, SpeechToTextService
from human_chat.voice.tts import GptSoVitsTtsService, TextToSpeechService


logger = get_logger(__name__)


@dataclass(frozen=True)
class VoiceResource:
    stt: SpeechToTextService | None
    tts: TextToSpeechService | None
    character: Character
    tts_process_owned: bool


@contextmanager
def open_voice_resource(
    settings: Settings,
    character: Character,
) -> Iterator[VoiceResource]:
    """Open voice clients and manage only the TTS process started here."""

    stt = _create_stt_service(settings)
    tts = _create_tts_service(settings, character)
    owned_process: subprocess.Popen | None = None

    try:
        if tts is not None:
            owned_process = _start_tts_if_requested(settings, tts)
        yield VoiceResource(
            stt=stt,
            tts=tts,
            character=character,
            tts_process_owned=owned_process is not None,
        )
    finally:
        if owned_process is not None:
            _stop_owned_process(owned_process)
        if tts is not None:
            tts.close()
        if stt is not None:
            stt.close()


def _create_stt_service(settings: Settings) -> SpeechToTextService | None:
    if not settings.openai_api_key.strip() or not settings.stt_model.strip():
        logger.warning("STT is unavailable because its API key or model is empty")
        return None
    return OpenAICompatibleSttService(
        api_key=settings.openai_api_key,
        model=settings.stt_model,
        base_url=settings.stt_base_url,
        timeout_seconds=settings.stt_timeout_seconds,
    )


def _create_tts_service(
    settings: Settings,
    character: Character,
) -> TextToSpeechService | None:
    if character.tts is None or not settings.tts_service_url.strip():
        return None
    return GptSoVitsTtsService(
        settings.tts_service_url,
        timeout_seconds=settings.tts_request_timeout_seconds,
    )


def _start_tts_if_requested(
    settings: Settings,
    service: TextToSpeechService,
) -> subprocess.Popen | None:
    if not settings.tts_auto_start:
        # No startup probe is needed in external-service mode. A background service
        # remains usable on demand, while an offline optional service cannot delay boot.
        return None
    # An already-running external service is never treated as a child process, even
    # when auto-start is enabled. HumanChat must not stop a process it does not own.
    if service.is_available():
        logger.info("Using existing TTS service at %s", settings.tts_service_url)
        return None

    directory = settings.gpt_sovits_dir
    python_executable = settings.gpt_sovits_python
    if directory is None or python_executable is None:
        raise RuntimeError(
            "TTS 自动启动需要配置 GPT_SOVITS_DIR 和 GPT_SOVITS_PYTHON。"
        )
    api_script = directory / settings.gpt_sovits_api_script
    if not directory.is_dir():
        raise RuntimeError(f"GPT-SoVITS 目录不存在：{directory}")
    if not python_executable.is_file():
        raise RuntimeError(f"GPT-SoVITS Python 不存在：{python_executable}")
    if not api_script.is_file():
        raise RuntimeError(f"GPT-SoVITS API 脚本不存在：{api_script}")

    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [str(python_executable), str(api_script)],
        cwd=str(directory),
        creationflags=creation_flags,
    )
    deadline = time.monotonic() + settings.tts_startup_timeout_seconds
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"TTS 服务启动失败，退出码：{process.returncode}"
                )
            if service.is_available():
                logger.info("Started managed TTS service")
                return process
            time.sleep(0.5)
        raise TimeoutError("TTS 服务启动超时。")
    except Exception:
        _stop_owned_process(process)
        raise


def _stop_owned_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    logger.info("Stopped managed TTS service")
