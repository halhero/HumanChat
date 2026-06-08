# HumanChat

HumanChat is an early-stage chat agent project that connects a chat model, a LangGraph workflow, and a local GPT-SoVITS TTS service.

## Project Structure

```text
HumanChat/
  main.py                 # Minimal entry point
  characters/
    nanami.yaml           # Default character profile
  human_chat/
    character.py          # Character profile loading and validation
    config.py             # Environment-based settings
    logging_config.py     # Logging setup helpers
    llm.py                # Chat model factory
    session_store.py      # JSON session persistence
    schemas.py            # Graph state and structured output schemas
    tts.py                # GPT-SoVITS HTTP client and service helpers
    graph.py              # LangGraph workflow
    cli.py                # Runtime helpers for one-shot and interactive execution
  speech/
    tmp.wav               # Generated speech output
  data/
    sessions/             # Saved chat sessions
```

## Setup

Install dependencies in your project environment:

```powershell
pip install -r requirements.txt
```

Create your local `.env` from `.env.example`, then fill in the real values:

```powershell
Copy-Item .env.example .env
```

Required:

```env
OPENAI_API_KEY="your_api_key_here"
```

If your GPT-SoVITS service is already running at `http://127.0.0.1:9880`, keep:

```env
HUMANCHAT_TTS_AUTO_START="false"
```

If you want HumanChat to start GPT-SoVITS automatically, set:

```env
HUMANCHAT_TTS_AUTO_START="true"
GPT_SOVITS_DIR="path/to/GPT-SoVITS"
GPT_SOVITS_PYTHON="path/to/python.exe"
```

## Characters

Characters are stored as YAML files in `characters/`.
The default character is `characters/nanami.yaml`.

A character controls the assistant prompt and TTS voice settings:

```yaml
id: nanami
name: 七海
reply_language: ja
system_prompt: |
  你是一个聊天助手，请你根据用户的问题以及提供的上下文给出适当的回复。
  你的回复应该自然、简短，并使用日语。
tts:
  ref_audio_path: path/to/ref.wav
  prompt_text: でも、怪しい人の手がかりならある。
  prompt_lang: ja
  text_lang: ja
  split_method: cut5
  speed_factor: 1.0
```

Switch characters by setting:

```env
HUMANCHAT_CHARACTER_PATH="characters/nanami.yaml"
```

## Run

```powershell
python main.py
```

The current entry point starts an interactive chat loop. Type `exit`, `quit`, `q`, or `退出` to stop.
On startup, HumanChat lets you create a new session, continue the latest session, or choose from recent sessions.
Session files are stored in `data/sessions/*.json` by default.

If the TTS service fails, HumanChat will keep the text reply and print a speech error instead of exiting the whole chat loop.

You can change the session directory with:

```env
HUMANCHAT_SESSION_DIR="data/sessions"
```

## Next Milestones

1. Add long-term memory based on saved sessions.
2. Add voice input for spoken conversations.
3. Add tool-calling nodes to the LangGraph workflow.
4. Add a simple UI after the core runtime is stable.
