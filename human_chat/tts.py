import subprocess
import time
from pathlib import Path

import requests
import simpleaudio as sa

from human_chat.character import Character
from human_chat.config import Settings
from human_chat.logging_config import get_logger


logger = get_logger(__name__)


class TtsError(RuntimeError):
    """Raised when speech synthesis or playback fails."""


class TtsClient:
    def __init__(self, settings: Settings, character: Character):
        self.settings = settings
        self.character = character

    def synthesize_and_play(self, text: str) -> None:
        output_path = Path(self.settings.speech_output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = requests.post(
                f"{self.settings.tts_service_url.rstrip('/')}/tts",
                json=self._payload(text),
                timeout=30,
            )
            response.raise_for_status()
            output_path.write_bytes(response.content)
        except requests.RequestException as exc:
            raise TtsError(f"TTS请求失败，请确认服务是否运行：{self.settings.tts_service_url}") from exc
        except OSError as exc:
            raise TtsError(f"TTS音频文件写入失败：{output_path}") from exc

        try:
            logger.info("Speech audio written to %s", output_path)
            wave_obj = sa.WaveObject.from_wave_file(str(output_path))
            play_obj = wave_obj.play()
            play_obj.wait_done()
            logger.info("Speech playback finished")
        except Exception as exc:
            raise TtsError("TTS音频播放失败") from exc

    def _payload(self, text: str) -> dict:
        tts = self.character.tts
        return {
            "ref_audio_path": tts.ref_audio_path,
            "prompt_text": tts.prompt_text,
            "prompt_lang": tts.prompt_lang,
            "text": text,
            "text_lang": tts.text_lang,
            "text_split_method": tts.split_method,
            "batch_size": 1,
            "speed_factor": tts.speed_factor,
        }


def start_tts_service(settings: Settings) -> subprocess.Popen:
    if settings.gpt_sovits_dir is None or settings.gpt_sovits_python is None:
        raise RuntimeError("GPT_SOVITS_DIR and GPT_SOVITS_PYTHON must be configured to auto-start TTS.")

    api_path = settings.gpt_sovits_dir / settings.gpt_sovits_api_script
    process = subprocess.Popen([str(settings.gpt_sovits_python), str(api_path)], cwd=str(settings.gpt_sovits_dir))
    start_time = time.time()

    while True:
        if process.poll() is not None:
            raise RuntimeError("TTS服务启动失败")

        try:
            response = requests.get(settings.tts_service_url, timeout=1)
            if response.status_code == 200:
                logger.info("TTS service started")
                return process
        except requests.RequestException:
            pass

        if time.time() - start_time > 10:
            raise TimeoutError("TTS服务启动超时")

        time.sleep(1)


def stop_tts_service(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    logger.info("TTS service stopped")
