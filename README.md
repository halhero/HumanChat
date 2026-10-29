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
    memory/                   # Records, formation, retrieval, context, jobs and persistence
    messages.py               # Shared source identity and provenance
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
GET  /sessions/{session_id}/turn       recover active or checkpointed review state
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

On reload, the browser queries the session's current turn rather than trusting a locally
cached turn id. Pending tool reviews are recovered from persistent LangGraph
checkpoints after a backend restart. A recovered review receives a new HTTP turn id;
the session id and checkpoint remain unchanged. Cancelling a pending phase clears its
Graph continuation without executing unapproved tools. Automatic memory maintenance has
no review phase and does not keep the conversation turn open.

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

Speech recognition defaults to Bailian Qwen ASR and reuses `OPENAI_API_KEY`:

```env
HUMANCHAT_STT_PROVIDER="dashscope"
HUMANCHAT_STT_MODEL="qwen3-asr-flash"
HUMANCHAT_STT_BASE_URL=""
HUMANCHAT_STT_TIMEOUT_SECONDS="60"
HUMANCHAT_STT_MAX_AUDIO_BYTES="26214400"
```

An empty STT base URL uses the China Bailian compatible endpoint for `dashscope`.
To use the multipart OpenAI transcription protocol instead, explicitly set
`HUMANCHAT_STT_PROVIDER="openai"`, a matching model such as `whisper-1`, and the
appropriate API key/base URL. The two providers do not share an audio request format.

Bailian's inline audio payload has a 10 MiB base64 limit. For this provider, the effective
raw upload limit is capped at 7 MiB (or a smaller configured value); the capabilities API
returns that effective limit so the browser and backend apply the same constraint.

Speech synthesis uses a GPT-SoVITS-compatible service:

```env
HUMANCHAT_TTS_SERVICE_URL="http://127.0.0.1:9880"
HUMANCHAT_TTS_AUTO_START="false"
```

`TTS_AUTO_START=false` does not disable speech synthesis. HumanChat still calls a service
that is already running at `TTS_SERVICE_URL`. Set it to `true` only when this process should
start and own a local GPT-SoVITS service, then also configure `GPT_SOVITS_DIR`,
`GPT_SOVITS_PYTHON`, and `GPT_SOVITS_API_SCRIPT`.

Loopback speech endpoints such as `localhost` and `127.0.0.1` bypass system HTTP proxies;
remote provider URLs continue to honor the standard proxy environment.

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

The current HTTP turn coordinator is process-local. Run one backend worker for this
single-user application; persistent checkpoints alone do not provide multi-worker locking
or automatic resumption of provider calls interrupted by a process crash.

## Long-Term Memory

Long-term memory follows this dependency direction:

```text
Before reply: Graph -> ContextBuilder -> hybrid recall -> task usefulness -> request budget
After reply:  Application -> durable job -> formation/review -> MemoryService -> Repository
                                                           -> SQLite / LangGraph Store
Search projection: SQLite FTS5 + sqlite-vec, or PostgreSQL FTS + pgvector HNSW
```

```env
HUMANCHAT_MEMORY_EXTRACTION_ENABLED="true"
HUMANCHAT_MEMORY_USER_ID="default"
HUMANCHAT_MEMORY_BACKEND="sqlite"
HUMANCHAT_MEMORY_PATH="data/memory/user_profile.sqlite"
```

Available backends are `sqlite`, `memory`, and `postgres`. Postgres also requires:

```env
HUMANCHAT_MEMORY_POSTGRES_URI="postgresql://..."
```

Completed replies register background memory jobs. New user messages are evidence;
previous messages only provide interpretation context. Extraction and semantic review
produce constrained, version-checked operations. Repeating a durable plan does not repeat
the write. Changes retain history; corrections revoke errors; explicit deletion removes
the record payload and its search projection.

Saving has no confirmation dialog, progress phase or saved-count event. Tool confirmation
is unchanged. Automatic maintenance is eventually consistent: an immediate next turn may
arrive before the background write finishes.

Retrieval combines indexed lexical search and real semantic embeddings. Local lexical ranking
uses FTS5 BM25; production vector search uses pgvector HNSW. The two bounded result lists are
merged with reciprocal-rank fusion before task applicability is judged. Only selected parent
IDs are read from canonical storage, and those references are rechecked when the final model
request is assembled. Current lightweight search documents also provide the task-background
catalog so non-topical collaboration constraints are not lost; this does not read canonical
records, but catalog size remains a separate relevance and token-budget concern.

```env
HUMANCHAT_MEMORY_SEMANTIC_ENABLED="true"
HUMANCHAT_MEMORY_EMBEDDING_MODEL="text-embedding-v4"
HUMANCHAT_MEMORY_EMBEDDING_BASE_URL=""
HUMANCHAT_MEMORY_EMBEDDING_API_KEY=""
HUMANCHAT_MEMORY_PROCESSING_TOKENS="16000"
HUMANCHAT_MEMORY_OUTPUT_TOKENS="4096"
HUMANCHAT_MEMORY_CANDIDATE_LIMIT="8"
HUMANCHAT_MEMORY_RECALL_LIMIT="24"
HUMANCHAT_MEMORY_HISTORY_TOKENS="3000"
HUMANCHAT_CHAT_CONTEXT_TOKENS="32000"
HUMANCHAT_CHAT_OUTPUT_TOKENS="2048"
```

Empty embedding URL/key reuse the configured chat endpoint/key. Use a provider-compatible
embedding model; requests cost provider tokens. Explicitly disabling semantic retrieval is
a degraded lexical-only mode, not equivalent semantic behavior. The tokenizer budget is
a conservative proxy, not the provider's exact token count. Size it below the actual model
Incomplete or unavailable semantic recall can defer automatic additions rather than treating
an empty result as proof that a record does not already exist.
Memory model calls use JSON mode, schema validation and a separate output limit. For the
current Qwen provider they explicitly disable thinking so reasoning cannot consume the
whole structured-output allowance; choose a model that supports this mode.

SQLite is the default canonical backend. The matching `.search.sqlite` and `.jobs.sqlite`
files beside the configured memory path contain the bounded search projection and durable
maintenance jobs. Canonical writes update only the affected search documents; application
startup and idle workers never scan all canonical memory. Existing JSON memory files are
left untouched and are not migrated automatically. Durable jobs require persistent checkpoints.
Run only one backend worker. PostgreSQL requires the `vector` extension; its remote service
is not covered by local SQLite checks.

Memory maintenance is internal to the Agent. The Web UI has no memory-management
button or dialog, and the public API has no `/api/v1/memories` routes. Explicit
corrections, retractions and deletion requests can be
interpreted from conversation by the formation pipeline and executed through
`MemoryService.apply`, with the same evidence, version and idempotency checks as other
automatic operations. Ambiguous requests may be deferred; this is not a guarantee
that every natural-language deletion request is understood or executed immediately.

Only tool operations have an approval flow. Compatibility handling for legacy
memory-approval checkpoints has been removed; upgrading does not automatically
resume or discard those obsolete continuations. No stored conversations or memories
are deleted by this code cleanup.

Deleting long-term memory does not erase the original conversation, existing checkpoints,
backups or provider records. This is a single-user application, not an authenticated
multi-tenant service. Step-by-step implementation and remaining limits are recorded in
`资料/63` through `资料/78` (the numbered Markdown documents); `资料/79` records
the subsequent removal of manual management and obsolete approval compatibility.

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
