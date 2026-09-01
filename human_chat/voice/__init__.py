"""Speech recognition and synthesis service boundaries."""

from human_chat.voice.stt import (
    OpenAICompatibleSttService,
    SpeechRecognitionError,
    SpeechToTextService,
)
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
]
