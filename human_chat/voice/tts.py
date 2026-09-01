"""Text-to-speech contracts and a GPT-SoVITS-compatible HTTP adapter."""

from dataclasses import dataclass
from typing import Protocol

import httpx

from human_chat.character import CharacterTtsConfig


class SpeechSynthesisError(RuntimeError):
    """Raised when speech synthesis fails or returns an invalid payload."""


@dataclass(frozen=True)
class SynthesizedAudio:
    content: bytes
    media_type: str


class TextToSpeechService(Protocol):
    def synthesize(
        self,
        text: str,
        voice: CharacterTtsConfig,
    ) -> SynthesizedAudio:
        ...

    def is_available(self) -> bool:
        ...

    def close(self) -> None:
        ...


class GptSoVitsTtsService:
    """Generate audio without writing files or playing sound on the server."""

    def __init__(
        self,
        service_url: str,
        *,
        timeout_seconds: float = 60,
    ) -> None:
        self._service_url = service_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds)

    def synthesize(
        self,
        text: str,
        voice: CharacterTtsConfig,
    ) -> SynthesizedAudio:
        normalized = text.strip()
        if not normalized:
            raise SpeechSynthesisError("待合成文本为空。")

        try:
            response = self._client.post(
                f"{self._service_url}/tts",
                json=self._payload(normalized, voice),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SpeechSynthesisError(
                f"TTS 服务调用失败：{self._service_url}"
            ) from exc

        if not response.content:
            raise SpeechSynthesisError("TTS 服务返回了空音频。")
        media_type = response.headers.get("content-type", "audio/wav")
        return SynthesizedAudio(
            content=response.content,
            media_type=media_type.split(";", maxsplit=1)[0].strip(),
        )

    def is_available(self) -> bool:
        try:
            response = self._client.get(self._service_url, timeout=2)
        except httpx.HTTPError:
            return False
        return response.status_code < 500

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _payload(text: str, voice: CharacterTtsConfig) -> dict:
        return {
            "ref_audio_path": voice.ref_audio_path,
            "prompt_text": voice.prompt_text,
            "prompt_lang": voice.prompt_lang,
            "text": text,
            "text_lang": voice.text_lang,
            "text_split_method": voice.split_method,
            "batch_size": 1,
            "speed_factor": voice.speed_factor,
        }
