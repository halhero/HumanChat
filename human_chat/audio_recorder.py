import wave
from pathlib import Path

from human_chat.config import Settings


def record_audio_file(settings: Settings) -> Path:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("麦克风录音需要安装 sounddevice，请运行 pip install -r requirements.txt。") from exc

    settings.audio_temp_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.audio_temp_dir / "input.wav"
    frame_count = int(settings.mic_record_seconds * settings.mic_sample_rate)

    print(f"开始录音，时长 {settings.mic_record_seconds} 秒...")
    recording = sd.rec(
        frame_count,
        samplerate=settings.mic_sample_rate,
        channels=1,
        dtype="int16",
    )
    sd.wait()

    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(settings.mic_sample_rate)
        wav_file.writeframes(recording.tobytes())

    return output_path
