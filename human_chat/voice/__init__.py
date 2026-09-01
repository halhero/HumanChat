"""Speech recognition and synthesis service boundaries."""

from human_chat.voice.stt import (
    OpenAICompatibleSttService,
    SpeechRecognitionError,
    SpeechToTextService,
)
from human_chat.voice.resources import VoiceResource, open_voice_resource
from human_chat.voice.tts import (
    GptSoVitsTtsService,
    SpeechSynthesisError,
    SynthesizedAudio,
    TextToSpeechService,
)


__all__ = [
    "GptSoVitsTtsService",
    "OpenAICompatibleSttService",
    "SpeechRecognitionError",
    "SpeechSynthesisError",
    "SpeechToTextService",
    "SynthesizedAudio",
    "TextToSpeechService",
    "VoiceResource",
    "open_voice_resource",
]
