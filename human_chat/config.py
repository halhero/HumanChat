import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_path(name: str, default: str | Path | None = None) -> Path | None:
    value = os.getenv(name)
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            return PROJECT_ROOT / path
        return path
    if default is None:
        return None
    path = Path(default).expanduser()
    if not path.is_absolute():
        return PROJECT_ROOT / path
    return path


class Settings(BaseModel):
    openai_api_key: str = Field(default="", description="API key for the chat model provider.")
    llm_model: str = "qwen3.5-flash"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    stt_model: str = "whisper-1"
    stt_base_url: str = ""
    memory_extraction_enabled: bool = True
    memory_user_id: str = "default"
    mic_record_seconds: int = 5
    mic_sample_rate: int = 16000

    character_path: Path = PROJECT_ROOT / "characters" / "nanami.yaml"
    audio_temp_dir: Path = PROJECT_ROOT / "data" / "audio"
    memory_path: Path = PROJECT_ROOT / "data" / "memory" / "user_profile.json"
    checkpoint_path: Path = PROJECT_ROOT / "data" / "checkpoints" / "langgraph.sqlite"
    speech_output_path: Path = PROJECT_ROOT / "speech" / "tmp.wav"
    session_dir: Path = PROJECT_ROOT / "data" / "sessions"

    tts_service_url: str = "http://127.0.0.1:9880"
    tts_auto_start: bool = False
    gpt_sovits_dir: Path | None = None
    gpt_sovits_python: Path | None = None
    gpt_sovits_api_script: str = "api_v2.py"

    tts_ref_audio_path: str = "D:/AgentStudy/Shinsekai/data/models/nanami/nanami.aac_0001620800_0001747840.wav"
    tts_prompt_text: str = "でも、怪しい人の手がかりならある。"
    tts_prompt_lang: str = "ja"
    tts_text_lang: str = "ja"
    tts_split_method: str = "cut5"
    tts_speed_factor: float = 1.0


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        llm_model=os.getenv("HUMANCHAT_LLM_MODEL", "qwen3.5-flash"),
        llm_base_url=os.getenv(
            "HUMANCHAT_LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        stt_model=os.getenv("HUMANCHAT_STT_MODEL", "whisper-1"),
        stt_base_url=os.getenv("HUMANCHAT_STT_BASE_URL", ""),
        memory_extraction_enabled=_env_bool("HUMANCHAT_MEMORY_EXTRACTION_ENABLED", True),
        memory_user_id=os.getenv("HUMANCHAT_MEMORY_USER_ID", "default"),
        mic_record_seconds=int(os.getenv("HUMANCHAT_MIC_RECORD_SECONDS", "5")),
        mic_sample_rate=int(os.getenv("HUMANCHAT_MIC_SAMPLE_RATE", "16000")),
        character_path=_env_path("HUMANCHAT_CHARACTER_PATH", PROJECT_ROOT / "characters" / "nanami.yaml"),
        audio_temp_dir=_env_path("HUMANCHAT_AUDIO_TEMP_DIR", PROJECT_ROOT / "data" / "audio"),
        memory_path=_env_path("HUMANCHAT_MEMORY_PATH", PROJECT_ROOT / "data" / "memory" / "user_profile.json"),
        checkpoint_path=_env_path(
            "HUMANCHAT_CHECKPOINT_PATH",
            PROJECT_ROOT / "data" / "checkpoints" / "langgraph.sqlite",
        ),
        speech_output_path=_env_path("HUMANCHAT_SPEECH_OUTPUT_PATH", PROJECT_ROOT / "speech" / "tmp.wav"),
        session_dir=_env_path("HUMANCHAT_SESSION_DIR", PROJECT_ROOT / "data" / "sessions"),
        tts_service_url=os.getenv("HUMANCHAT_TTS_SERVICE_URL", "http://127.0.0.1:9880"),
        tts_auto_start=_env_bool("HUMANCHAT_TTS_AUTO_START", False),
        gpt_sovits_dir=_env_path("GPT_SOVITS_DIR"),
        gpt_sovits_python=_env_path("GPT_SOVITS_PYTHON"),
        gpt_sovits_api_script=os.getenv("GPT_SOVITS_API_SCRIPT", "api_v2.py"),
        tts_ref_audio_path=os.getenv(
            "HUMANCHAT_TTS_REF_AUDIO_PATH",
            "D:/AgentStudy/Shinsekai/data/models/nanami/nanami.aac_0001620800_0001747840.wav",
        ),
        tts_prompt_text=os.getenv(
            "HUMANCHAT_TTS_PROMPT_TEXT",
            "でも、怪しい人の手がかりならある。",
        ),
        tts_prompt_lang=os.getenv("HUMANCHAT_TTS_PROMPT_LANG", "ja"),
        tts_text_lang=os.getenv("HUMANCHAT_TTS_TEXT_LANG", "ja"),
        tts_split_method=os.getenv("HUMANCHAT_TTS_SPLIT_METHOD", "cut5"),
        tts_speed_factor=float(os.getenv("HUMANCHAT_TTS_SPEED_FACTOR", "1.0")),
    )
