# HumanChat

HumanChat is a Web-oriented chat Agent built with FastAPI, LangGraph, LangChain,
long-term memory, managed checkpoints, local tools, and optional MCP servers.

The project is being migrated to a separated frontend/backend architecture. The Python
process is now the backend application only; the former interactive CLI and local audio
input/playback path have been removed.

## Architecture

```text
Frontend (next stages)
        |
        | HTTP / event stream
        v
FastAPI adapter
        |
        v
HumanChatApplication
  - session use cases
  - resource ownership
  - safe status snapshot
        |
        +--> LangGraph + Checkpointer
        +--> MemoryService
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
    checkpointing.py          # Managed LangGraph checkpointer
    character.py              # Character prompt model and loader
    config.py                 # Environment settings
    graph.py                  # LangGraph Agent workflow
    memory_*.py               # Long-term memory domain and persistence
    mcp_*.py                  # MCP configuration and providers
    session_*.py              # Session model and repository contract
    storage/                  # Persistence composition
    tool_*.py / tools.py      # Tool registry, policies, and local tools
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

## Character

The character file contains only behavior relevant to text conversation:

```yaml
id: nanami
name: 七海
reply_language: ja
system_prompt: |
  你是一个聊天助手，请你根据用户的问题以及提供的上下文给出适当的回复。
```

Select another character prompt with:

```env
HUMANCHAT_CHARACTER_PATH="characters/nanami.yaml"
```

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

Automatic extraction and user review remain LangGraph nodes. The Web review endpoint will
be added with the conversation API in the next stage.

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

Next:

1. Versioned session and conversation APIs.
2. Event-streamed replies, cancellation, and Graph interrupt review.
3. A small React frontend for the basic chat workflow.
