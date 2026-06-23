from human_chat.config import Settings
from human_chat.audio_recorder import record_audio_file
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


class MicrophoneInputProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def read_question(self) -> str:
        value = input("\n按 Enter 开始录音，或输入命令：").strip()
        if value:
            return value

        while True:
            audio_path = record_audio_file(self.settings)
            print("正在识别录音...")
            text = transcribe_audio_file(self.settings, audio_path)
            print(f"识别结果：{text}")
            choice = input("发送？y=发送 / n=取消 / r=重录：").strip().lower()
            if choice == "y":
                return text
            if choice == "r":
                continue
            return ""
