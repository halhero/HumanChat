from human_chat.config import Settings
from human_chat.stt import transcribe_audio_file


class TextInputProvider:
    def read_question(self) -> str:
        return input("\n你：").strip()


class AudioFileInputProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def read_question(self) -> str:
        value = input("\n音频文件路径或命令：").strip()
        if not value or value.startswith("/") or value.lower() in {"exit", "quit", "q"} or value == "退出":
            return value
        print("正在识别音频...")
        return transcribe_audio_file(self.settings, value)
