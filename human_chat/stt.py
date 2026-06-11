from pathlib import Path

from human_chat.config import Settings


def transcribe_audio_file(settings: Settings, audio_path: str | Path) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("语音识别需要安装 openai 依赖，请运行 pip install -r requirements.txt。") from exc

    path = Path(audio_path).expanduser()
    if not path.is_file():
        raise ValueError(f"音频文件不存在：{path}")

    client_kwargs = {"api_key": settings.openai_api_key}
    if settings.stt_base_url:
        client_kwargs["base_url"] = settings.stt_base_url

    client = OpenAI(**client_kwargs)
    with path.open("rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model=settings.stt_model,
            file=audio_file,
        )

    text = getattr(transcript, "text", "")
    if not text:
        raise RuntimeError("语音识别没有返回文本。")
    return text.strip()
