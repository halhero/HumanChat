"""Backend-neutral speech-to-text service contracts and provider adapter."""

from typing import Protocol

from openai import OpenAI


class SpeechRecognitionError(RuntimeError):
    """Raised when an audio payload cannot be transcribed."""


class SpeechToTextService(Protocol):
    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: str,
    ) -> str:
        ...

    def close(self) -> None:
        ...


class OpenAICompatibleSttService:
    """Transcribe in-memory audio through an OpenAI-compatible endpoint.

    The service accepts bytes rather than a local path so HTTP uploads, future mobile
    clients, and background jobs can share the same application capability.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "",
        timeout_seconds: float = 60,
    ) -> None:
        client_options = {
            "api_key": api_key,
            "timeout": timeout_seconds,
        }
        if base_url.strip():
            client_options["base_url"] = base_url.rstrip("/")
        self._client = OpenAI(**client_options)
        self._model = model

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: str,
    ) -> str:
        if not audio:
            raise SpeechRecognitionError("音频内容为空。")

        try:
            transcript = self._client.audio.transcriptions.create(
                model=self._model,
                file=(filename, audio, content_type),
            )
        except Exception as exc:
            raise SpeechRecognitionError("语音识别服务调用失败。") from exc

        text = str(getattr(transcript, "text", "")).strip()
        if not text:
            raise SpeechRecognitionError("语音识别服务没有返回文本。")
        return text

    def close(self) -> None:
        self._client.close()
