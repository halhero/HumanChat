"""Backend-neutral speech-to-text service contracts and provider adapter."""

import base64
from typing import Protocol

import httpx
from openai import OpenAI

from human_chat.voice.http import should_trust_environment_proxy


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


class _OpenAISttClient:
    """Share SDK client ownership, not the providers' different audio protocols."""

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
            normalized_base_url = base_url.rstrip("/")
            client_options["base_url"] = normalized_base_url
            if not should_trust_environment_proxy(normalized_base_url):
                client_options["http_client"] = httpx.Client(
                    timeout=timeout_seconds,
                    trust_env=False,
                )
        self._client = OpenAI(**client_options)
        self._model = model

    def close(self) -> None:
        self._client.close()


class OpenAICompatibleSttService(_OpenAISttClient):
    """Transcribe uploaded bytes through the multipart transcription endpoint."""

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


class DashScopeSttService(_OpenAISttClient):
    """Qwen ASR uses chat completions with input_audio, not /audio/transcriptions.

    A data URL keeps browser uploads in memory and avoids publishing audio to an
    external file host. The API key is sent only to the configured provider.
    """

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: str,
    ) -> str:
        if not audio:
            raise SpeechRecognitionError("音频内容为空。")
        # Qwen's inline audio limit is 10 MiB including the base64 expansion.
        if 4 * ((len(audio) + 2) // 3) > 10 * 1024 * 1024:
            raise SpeechRecognitionError("音频超过百炼内联识别的大小限制。")
        mime_type = content_type.split(";", maxsplit=1)[0].strip().lower()
        mime_type = {
            "audio/x-wav": "audio/wav",
            "audio/mp3": "audio/mpeg",
            "audio/m4a": "audio/mp4",
            "audio/x-m4a": "audio/mp4",
        }.get(mime_type, mime_type)
        encoded = base64.b64encode(audio).decode("ascii")
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": [{
                        "type": "input_audio",
                        "input_audio": {"data": f"data:{mime_type};base64,{encoded}"},
                    }],
                }],
                stream=False,
                extra_body={"asr_options": {"enable_itn": False}},
            )
        except Exception as exc:
            raise SpeechRecognitionError("百炼语音识别服务调用失败。") from exc
        text = completion.choices[0].message.content if completion.choices else None
        if not isinstance(text, str) or not text.strip():
            raise SpeechRecognitionError("语音识别服务没有返回文本。")
        return text.strip()
