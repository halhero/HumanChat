"""Voice capability, transcription, and speech synthesis endpoints."""

from pathlib import PurePath

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from human_chat.api.dependencies import HumanChatApplicationDependency
from human_chat.api.errors import ApiError
from human_chat.api.models import (
    SpeechSynthesisRequest,
    TranscriptionResponse,
    VoiceCapabilitiesResponse,
)
from human_chat.logging_config import get_logger
from human_chat.voice import SpeechRecognitionError, SpeechSynthesisError


router = APIRouter(prefix="/voice", tags=["voice"])
logger = get_logger(__name__)

_SUPPORTED_AUDIO_TYPES = {
    "audio/flac",
    "audio/m4a",
    "audio/mp3",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
}


@router.get("/capabilities", response_model=VoiceCapabilitiesResponse)
def get_voice_capabilities(
    request: Request,
    application: HumanChatApplicationDependency,
) -> VoiceCapabilitiesResponse:
    capabilities = application.voice_capabilities()
    return VoiceCapabilitiesResponse(
        stt_enabled=capabilities.stt_enabled,
        tts_enabled=capabilities.tts_enabled,
        tts_available=capabilities.tts_available,
        tts_auto_start=capabilities.tts_auto_start,
        max_audio_bytes=request.app.state.settings.stt_max_audio_bytes,
    )


@router.post("/transcriptions", response_model=TranscriptionResponse)
async def transcribe_audio(
    request: Request,
    application: HumanChatApplicationDependency,
    audio: UploadFile = File(...),
) -> TranscriptionResponse:
    content_type = (audio.content_type or "").split(";", maxsplit=1)[0].lower()
    if content_type not in _SUPPORTED_AUDIO_TYPES:
        await audio.close()
        raise ApiError(415, "unsupported_audio_type", "不支持该音频格式。")

    limit = request.app.state.settings.stt_max_audio_bytes
    try:
        content = await audio.read(limit + 1)
    finally:
        await audio.close()
    if len(content) > limit:
        raise ApiError(413, "audio_too_large", "音频文件超过大小限制。")
    if not content:
        raise ApiError(400, "empty_audio", "音频内容为空。")

    filename = _safe_filename(audio.filename)
    try:
        text = await run_in_threadpool(
            application.transcribe_audio,
            content,
            filename=filename,
            content_type=content_type,
        )
    except SpeechRecognitionError as exc:
        logger.warning("Speech transcription failed", exc_info=True)
        raise ApiError(503, "transcription_failed", str(exc)) from exc
    return TranscriptionResponse(text=text)


@router.post("/speech")
def synthesize_speech(
    payload: SpeechSynthesisRequest,
    application: HumanChatApplicationDependency,
) -> Response:
    try:
        audio = application.synthesize_speech(payload.text)
    except SpeechSynthesisError as exc:
        logger.warning("Speech synthesis failed", exc_info=True)
        raise ApiError(503, "speech_synthesis_failed", str(exc)) from exc
    return Response(
        content=audio.content,
        media_type=audio.media_type,
        headers={"Cache-Control": "no-store"},
    )


def _safe_filename(value: str | None) -> str:
    normalized = (value or "recording.webm").replace("\\", "/")
    name = PurePath(normalized).name.strip()
    return name[:255] or "recording.webm"
