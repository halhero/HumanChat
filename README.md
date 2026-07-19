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
    audio_recorder.py     # Microphone recording helper
    config.py             # Environment-based settings
    input_provider.py     # Text and audio-file input providers
    logging_config.py     # Logging setup helpers
    llm.py                # Chat model factory
    memory_models.py      # Long-term memory data models
    memory_repository.py  # JSON and LangGraph Store persistence adapters
    memory_service.py     # Long-term memory business rules
    runtime.py            # Conversation runtime orchestration
    session_store.py      # JSON session persistence
    schemas.py            # Graph state and structured output schemas
    stt.py                # Speech-to-text helpers
    storage/              # Storage composition and session adapter
    tools.py              # Safe local project tools
    tts.py                # GPT-SoVITS HTTP client and service helpers
    graph.py              # LangGraph workflow
    cli.py                # Runtime helpers for one-shot and interactive execution
  speech/
    tmp.wav               # Generated speech output
  data/
    memory/               # Long-term memory templates and local memory
    sessions/             # Saved chat sessions
```

## Setup

Install dependencies in your project environment:

```powershell
pip install -r requirements.txt
```

For development and tests:

```powershell
pip install -r requirements-dev.txt
python -m pytest
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

## Long-Term Memory

Long-term memory stores stable user and project information across sessions.
The default memory path is:

```env
HUMANCHAT_MEMORY_PATH="data/memory/user_profile.json"
HUMANCHAT_MEMORY_USER_ID="default"
```

If the file does not exist, HumanChat creates it from the built-in default memory.
Real memory files are ignored by Git; use `data/memory/user_profile.example.json` as a template.
The default user keeps using `user_profile.json`. Other `HUMANCHAT_MEMORY_USER_ID` values are stored in separate JSON files derived from the same base path.

Long-term memory is injected into the system prompt together with the selected character profile.
The graph reads long-term memory on each chat turn, so `/memory add` and `/memory delete` can affect later replies without restarting the app.

The memory module follows a one-way dependency structure:

```text
Graph / CLI -> MemoryService -> MemoryRepository -> JSON or LangGraph Store
                                      |
                                  MemoryModel
```

`MemoryService` owns validation, deduplication, deletion, and prompt formatting.
`MemoryRepository` owns persistence, while the model classes contain no file or business operations.

During chat, manage long-term memory with:

```text
/memory
/memory add 用户希望先看设计再改代码
/memory delete 1
```

When enabled, HumanChat also proposes long-term memory candidates after each normal chat turn and asks for confirmation before saving:

```env
HUMANCHAT_MEMORY_EXTRACTION_ENABLED="true"
```

The graph raises a structured memory review interrupt, and the CLI resumes the graph with an explicit user decision before long-term memory is written.

## Short-Term Memory

Short-term conversation state is managed by the LangGraph checkpointer with the active session id as `thread_id`.
By default HumanChat uses:

```env
HUMANCHAT_CHECKPOINT_PATH="data/checkpoints/langgraph.sqlite"
```

When `langgraph-checkpoint-sqlite` is installed, this SQLite checkpoint file lets chat state survive process restarts.
If the SQLite checkpointer package is unavailable, HumanChat falls back to an in-memory checkpointer and logs that the state is not restart-persistent.

## Project Tools

HumanChat includes safe, read-only project tools exposed through the shared tool provider.
The agent graph and the CLI commands use the same tool source:

```text
/tools
/files
/read human_chat/graph.py
/search memory
```

These tools are limited to files inside the project directory.
The agent graph can decide to call these read-only tools before generating a reply when a question requires project context.
In chat, `/tools` shows the currently registered tool metadata, including source, safety level, and usage.

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

## Voice Input

HumanChat supports audio-file input and a first microphone input mode.
On startup, choose `音频文件输入` or `麦克风输入`. The recognized text is sent through the same chat runtime as normal text input.

Switch input mode during a chat:

```text
/input text
/input audio-file
/input mic
```

Enable lightweight graph debugging during a chat:

```text
/debug on
/debug off
```

Debug mode prints the returned graph state summary after each chat turn.

Configure STT with:

```env
HUMANCHAT_STT_MODEL="whisper-1"
HUMANCHAT_STT_BASE_URL=""
HUMANCHAT_MIC_RECORD_SECONDS="5"
HUMANCHAT_MIC_SAMPLE_RATE="16000"
```

## Next Milestones

1. Expand automated tests to runtime and graph behavior with mocked models.
2. Add SQLite-backed storage adapters.
3. Improve automatic memory extraction quality and review UX.
4. Build a simple UI after the core runtime is stable.
