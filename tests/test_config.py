from human_chat import config


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
    monkeypatch.setenv("HUMANCHAT_MIC_RECORD_SECONDS", "8")
    monkeypatch.setenv("HUMANCHAT_CHARACTER_PATH", "characters/test.yaml")
    monkeypatch.setenv("GPT_SOVITS_DIR", "")

    loaded = config.load_settings()

    assert not loaded.memory_extraction_enabled
    assert loaded.mic_record_seconds == 8
    assert loaded.character_path == config.PROJECT_ROOT / "characters" / "test.yaml"
    assert loaded.gpt_sovits_dir is None
