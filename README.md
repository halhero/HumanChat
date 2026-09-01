# HumanChat

HumanChat is a Web-oriented chat Agent built with FastAPI, LangGraph, LangChain,
long-term memory, managed checkpoints, local tools, and optional MCP servers.

The project uses a separated frontend/backend architecture. The Python process owns Agent
resources and speech provider clients; microphone capture and audio playback stay in the
browser where the user's devices actually live.

## Architecture

```text
React + TypeScript frontend
        |
        | JSON API / Server-Sent Events
        v
FastAPI adapter
        |
        v
ConversationService       HumanChatApplication
  - active turns            - session use cases
  - stream/cancel/review     - resource ownership
  - browser event protocol  - safe graph boundary
        |
        +--> LangGraph + Checkpointer
        +--> MemoryService
        +--> VoiceResource -> STT / GPT-SoVITS-compatible TTS
        +--> ToolRegistry
                 +--> local tools
                 +--> MCP tools
```

`HumanChatApplication` is the backend boundary. FastAPI does not access repositories,
checkpointers, memory stores, or tool providers directly.

## Project Structure

```text
HumanChat/
  characters/                 # Character prompts
  config/                     # MCP configuration example
  data/                       # Local runtime data
  human_chat/
    api/                      # FastAPI factory, dependencies, routes, schemas
    application.py            # Application use cases and resource composition
    conversation/             # Active turn state and browser event protocol
    checkpointing.py          # Managed LangGraph checkpointer
    character.py              # Character prompt model and loader
    config.py                 # Environment settings
    graph.py                  # LangGraph Agent workflow
    memory_*.py               # Long-term memory domain and persistence
    voice/                    # STT, TTS, and optional local TTS process lifecycle
    mcp_*.py                  # MCP configuration and providers
    session_*.py              # Session model and repository contract
    storage/                  # Persistence composition
    tool_*.py / tools.py      # Tool registry, policies, and local tools
  web/                        # React, TypeScript, Vite frontend
  tests/                      # Current backend regression tests
  资料/                       # Design and change records
```

## Setup

Install dependencies:

```powershell
pip install -r requirements.txt
```

For development checks:

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

Create local settings:

```powershell
Copy-Item .env.example .env
```

Set at least the model API key:

```env
OPENAI_API_KEY="your_api_key_here"
```

The default model endpoint is OpenAI-compatible and can be changed with:

```env
HUMANCHAT_LLM_MODEL="qwen3.5-flash"
HUMANCHAT_LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
```

## Run Backend

```powershell
python -m human_chat.api
```

The default address is `http://127.0.0.1:8000`. Readiness endpoint:

```text
GET http://127.0.0.1:8000/api/v1/health
```

Backend bind and allowed frontend origins are configurable:

```env
HUMANCHAT_API_HOST="127.0.0.1"
HUMANCHAT_API_PORT="8000"
HUMANCHAT_API_CORS_ORIGINS="http://127.0.0.1:5173,http://localhost:5173"
```

FastAPI lifespan opens the checkpointer, memory resource, MCP bridge, tool registry, and
compiled graph once per process, then closes them in reverse order on shutdown.

## Run Frontend

Use Node.js `20.19+` or `22.12+`, then install and start the Vite development server:

```powershell
Set-Location web
pnpm install
pnpm dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` requests to the backend at
`http://127.0.0.1:8000`, so the backend should be running at the same time. A different
API prefix can be supplied with `VITE_API_BASE_URL`.

Build the production frontend, then start only FastAPI:

```powershell
pnpm build
Set-Location ..
python -m human_chat.api
```

The complete application is then available at `http://127.0.0.1:8000`. Hashed Vite
assets receive long-lived cache headers, while `index.html` is never cached. Override the
build directory only when deployment uses a different layout:

```env
HUMANCHAT_FRONTEND_DIST_PATH="web/dist"
```

### Conversation API

All browser endpoints are versioned under `/api/v1`:

```text
GET  /sessions                         list recent sessions
POST /sessions                         create a session
GET  /sessions/{session_id}            read metadata and message history
POST /sessions/{session_id}/turns      start a turn as an SSE response
GET  /turns/{turn_id}                  read active or retained turn status
POST /turns/{turn_id}/decision         resume a review as an SSE response
POST /turns/{turn_id}/cancel           request cooperative cancellation
GET  /voice/capabilities               discover optional speech features
POST /voice/transcriptions             upload audio and receive text
POST /voice/speech                     synthesize text as audio
```

Conversation streams send stable public events such as `turn.progress`,
`message.completed`, `review.required`, `turn.completed`, `turn.cancelled`, and
`turn.failed`. Internal graph state and routine tool-call traces are not part of the
browser contract. The response header `X-Turn-ID` identifies the turn for status,
review, and cancellation requests.

## Character

The character file keeps text behavior and an optional voice profile:

```yaml
id: nanami
name: 七海
reply_language: ja
system_prompt: |
  你是一个聊天助手，请你根据用户的问题以及提供的上下文给出适当的回复。
tts:
  ref_audio_path: D:/path/to/reference.wav
  prompt_text: 参考音频对应的文本
  prompt_lang: ja
  text_lang: ja
  split_method: cut5
  speed_factor: 1.0
```

Select another character prompt with:

```env
HUMANCHAT_CHARACTER_PATH="characters/nanami.yaml"
```

## Voice

Speech recognition uses an OpenAI-compatible transcription endpoint:

```env
HUMANCHAT_STT_MODEL="whisper-1"
HUMANCHAT_STT_BASE_URL=""
HUMANCHAT_STT_TIMEOUT_SECONDS="60"
HUMANCHAT_STT_MAX_AUDIO_BYTES="26214400"
```

Speech synthesis uses a GPT-SoVITS-compatible service:

```env
HUMANCHAT_TTS_SERVICE_URL="http://127.0.0.1:9880"
HUMANCHAT_TTS_AUTO_START="false"
```

`TTS_AUTO_START=false` does not disable speech synthesis. HumanChat still calls a service
that is already running at `TTS_SERVICE_URL`. Set it to `true` only when this process should
start and own a local GPT-SoVITS service, then also configure `GPT_SOVITS_DIR`,
`GPT_SOVITS_PYTHON`, and `GPT_SOVITS_API_SCRIPT`.

The Web client can record the browser microphone, upload an existing audio file, place the
transcription in the composer for review, and play synthesized audio for assistant messages.

## Conversation State

Short-term conversation state is owned by the LangGraph checkpointer. The session id is
used as the Graph `thread_id`.

```env
HUMANCHAT_CHECKPOINT_BACKEND="sqlite"
HUMANCHAT_CHECKPOINT_ALLOW_MEMORY_FALLBACK="false"
HUMANCHAT_CHECKPOINT_PATH="data/checkpoints/langgraph.sqlite"
HUMANCHAT_SESSION_DIR="data/sessions"
```

SQLite is persistent across backend restarts. Memory fallback is opt-in because it cannot
recover a conversation after process exit.

## Long-Term Memory

Long-term memory follows this dependency direction:

```text
Graph -> MemoryService -> MemoryRepository -> JSON / LangGraph Store
```

```env
HUMANCHAT_MEMORY_EXTRACTION_ENABLED="true"
HUMANCHAT_MEMORY_USER_ID="default"
HUMANCHAT_MEMORY_BACKEND="json"
HUMANCHAT_MEMORY_PATH="data/memory/user_profile.json"
```

Available backends are `json`, `memory`, and `postgres`. Postgres also requires:

```env
HUMANCHAT_MEMORY_POSTGRES_URI="postgresql://..."
```

Automatic extraction and user review remain LangGraph nodes. When confirmation is needed,
the stream emits `review.required`; the browser can approve selected candidates through the
turn decision endpoint and LangGraph resumes from its checkpoint.

## Tools And MCP

The Graph receives all tools from one `ToolRegistry`. Local project tools are available by
default; optional MCP tools use the same registration and safety policy model.

Enable MCP after copying the example configuration:

```powershell
Copy-Item config/mcp_servers.example.json config/mcp_servers.json
```

```env
HUMANCHAT_MCP_ENABLED="true"
HUMANCHAT_MCP_CONFIG_PATH="config/mcp_servers.json"
HUMANCHAT_MCP_FAIL_FAST="false"
```

Enabled MCP servers are discovered concurrently. Tool names are prefixed with the server
name, sensitive arguments are redacted for review, and tools marked as potentially writable
must be explicitly approved before LangGraph executes them.

## Current Migration State

Completed:

1. FastAPI process foundation and managed application lifespan.
2. Application-owned resources and removal of the obsolete CLI/audio execution path.
3. Session/history APIs, SSE conversation turns, cooperative cancellation, and LangGraph
   interrupt review.
4. A compact React frontend with session navigation, streaming replies, cancellation,
   review dialogs, and responsive mobile layout.
5. Browser-based recording, audio-file transcription, per-message speech playback, and
   optional automatic reading without server-side device access.

Next:

1. Authentication and multi-user isolation when the project starts serving multiple users.
2. Broader automated coverage and deployment automation as the product surface grows.
