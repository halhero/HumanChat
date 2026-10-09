from human_chat import config
import pytest


def _isolate_environment(monkeypatch) -> None:
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for _, env_name, _ in config._ENV_OVERRIDES:
        monkeypatch.delenv(env_name, raising=False)


def test_load_settings_uses_model_defaults(monkeypatch):
    _isolate_environment(monkeypatch)

    loaded = config.load_settings()

    assert loaded == config.Settings()


def test_load_settings_applies_typed_environment_overrides(monkeypatch):
    _isolate_environment(monkeypatch)
    monkeypatch.setenv("HUMANCHAT_MEMORY_EXTRACTION_ENABLED", "false")
    monkeypatch.setenv("HUMANCHAT_CHARACTER_PATH", "characters/test.yaml")

    loaded = config.load_settings()

    assert not loaded.memory_extraction_enabled
    assert loaded.character_path == config.PROJECT_ROOT / "characters" / "test.yaml"


def test_stt_provider_defaults_and_upload_limit():
    assert config.Settings().stt_provider == "dashscope"
    assert config.Settings().stt_model == "qwen3-asr-flash"
    assert config.Settings().stt_upload_limit_bytes == 7 * 1024 * 1024
    assert config.Settings(stt_provider="openai").stt_upload_limit_bytes == 25 * 1024 * 1024
    with pytest.raises(ValueError):
        config.Settings(stt_provider="unknown")
