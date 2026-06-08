from pathlib import Path

import yaml
from pydantic import BaseModel


class CharacterTtsConfig(BaseModel):
    ref_audio_path: str
    prompt_text: str
    prompt_lang: str = "ja"
    text_lang: str = "ja"
    split_method: str = "cut5"
    speed_factor: float = 1.0


class Character(BaseModel):
    id: str
    name: str
    reply_language: str = "ja"
    system_prompt: str
    tts: CharacterTtsConfig


def load_character(path: Path) -> Character:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data:
        raise ValueError(f"Character config is empty: {path}")
    return Character(**data)
