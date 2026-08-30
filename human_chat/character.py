from pathlib import Path

import yaml
from pydantic import BaseModel


class Character(BaseModel):
    id: str
    name: str
    reply_language: str = "ja"
    system_prompt: str


def load_character(path: Path) -> Character:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data:
        raise ValueError(f"Character config is empty: {path}")
    return Character(**data)
