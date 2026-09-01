import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_UNSET = object()


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_path(value: str):
    if not value.strip():
        return _UNSET
    path = Path(value).expanduser()
    if not path.is_absolute():
        return PROJECT_ROOT / path
    return path


def _parse_optional_path(value: str) -> Path | None:
    if not value.strip():
        return None
    parsed = _parse_path(value)
    return None if parsed is _UNSET else parsed


class Settings(BaseModel):
    openai_api_key: str = Field(default="", description="API key for the chat model provider.")
    llm_model: str = "qwen3.5-flash"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_timeout_seconds: float = Field(default=60, ge=1, le=600)
    stt_model: str = "whisper-1"
    stt_base_url: str = ""
    stt_timeout_seconds: float = Field(default=60, ge=1, le=600)
    memory_extraction_enabled: bool = True
    memory_user_id: str = "default"
    memory_backend: str = "json"
    memory_postgres_uri: str = ""
    checkpoint_backend: str = "sqlite"
    checkpoint_allow_memory_fallback: bool = False
    mcp_enabled: bool = False
    mcp_fail_fast: bool = False
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    frontend_dist_path: Path = PROJECT_ROOT / "web" / "dist"

    character_path: Path = PROJECT_ROOT / "characters" / "nanami.yaml"
    memory_path: Path = PROJECT_ROOT / "data" / "memory" / "user_profile.json"
    checkpoint_path: Path = PROJECT_ROOT / "data" / "checkpoints" / "langgraph.sqlite"
    mcp_config_path: Path = PROJECT_ROOT / "config" / "mcp_servers.json"
    session_dir: Path = PROJECT_ROOT / "data" / "sessions"

    tts_service_url: str = "http://127.0.0.1:9880"
    tts_auto_start: bool = False
    tts_request_timeout_seconds: float = Field(default=60, ge=1, le=600)
    tts_startup_timeout_seconds: float = Field(default=30, ge=1, le=300)
    gpt_sovits_dir: Path | None = None
    gpt_sovits_python: Path | None = None
    gpt_sovits_api_script: str = "api_v2.py"


_ENV_OVERRIDES = (
    ("openai_api_key", "OPENAI_API_KEY", str),
    ("llm_model", "HUMANCHAT_LLM_MODEL", str),
    ("llm_base_url", "HUMANCHAT_LLM_BASE_URL", str),
    ("llm_timeout_seconds", "HUMANCHAT_LLM_TIMEOUT_SECONDS", float),
    ("stt_model", "HUMANCHAT_STT_MODEL", str),
    ("stt_base_url", "HUMANCHAT_STT_BASE_URL", str),
    ("stt_timeout_seconds", "HUMANCHAT_STT_TIMEOUT_SECONDS", float),
    (
        "memory_extraction_enabled",
        "HUMANCHAT_MEMORY_EXTRACTION_ENABLED",
        _parse_bool,
    ),
    ("memory_user_id", "HUMANCHAT_MEMORY_USER_ID", str),
    ("memory_backend", "HUMANCHAT_MEMORY_BACKEND", str),
    ("memory_postgres_uri", "HUMANCHAT_MEMORY_POSTGRES_URI", str),
    ("checkpoint_backend", "HUMANCHAT_CHECKPOINT_BACKEND", str),
    (
        "checkpoint_allow_memory_fallback",
        "HUMANCHAT_CHECKPOINT_ALLOW_MEMORY_FALLBACK",
        _parse_bool,
    ),
    ("mcp_enabled", "HUMANCHAT_MCP_ENABLED", _parse_bool),
    ("mcp_fail_fast", "HUMANCHAT_MCP_FAIL_FAST", _parse_bool),
    ("api_host", "HUMANCHAT_API_HOST", str),
    ("api_port", "HUMANCHAT_API_PORT", int),
    (
        "api_cors_origins",
        "HUMANCHAT_API_CORS_ORIGINS",
        lambda value: [
            origin.strip()
            for origin in value.split(",")
            if origin.strip()
        ],
    ),
    ("frontend_dist_path", "HUMANCHAT_FRONTEND_DIST_PATH", _parse_path),
    ("character_path", "HUMANCHAT_CHARACTER_PATH", _parse_path),
    ("memory_path", "HUMANCHAT_MEMORY_PATH", _parse_path),
    ("checkpoint_path", "HUMANCHAT_CHECKPOINT_PATH", _parse_path),
    ("mcp_config_path", "HUMANCHAT_MCP_CONFIG_PATH", _parse_path),
    ("session_dir", "HUMANCHAT_SESSION_DIR", _parse_path),
    ("tts_service_url", "HUMANCHAT_TTS_SERVICE_URL", str),
    ("tts_auto_start", "HUMANCHAT_TTS_AUTO_START", _parse_bool),
    (
        "tts_request_timeout_seconds",
        "HUMANCHAT_TTS_REQUEST_TIMEOUT_SECONDS",
        float,
    ),
    (
        "tts_startup_timeout_seconds",
        "HUMANCHAT_TTS_STARTUP_TIMEOUT_SECONDS",
        float,
    ),
    ("gpt_sovits_dir", "GPT_SOVITS_DIR", _parse_optional_path),
    ("gpt_sovits_python", "GPT_SOVITS_PYTHON", _parse_optional_path),
    ("gpt_sovits_api_script", "GPT_SOVITS_API_SCRIPT", str),
)


def load_settings() -> Settings:
    load_dotenv()
    values = {}
    for field_name, env_name, parser in _ENV_OVERRIDES:
        raw_value = os.getenv(env_name)
        if raw_value is None:
            continue
        parsed_value = parser(raw_value)
        if parsed_value is not _UNSET:
            values[field_name] = parsed_value
    return Settings(**values)
