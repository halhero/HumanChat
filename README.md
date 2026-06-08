# HumanChat

HumanChat is an early-stage chat agent project that connects a chat model, a LangGraph workflow, and a local GPT-SoVITS TTS service.

## Project Structure

```text
HumanChat/
  main.py                 # Minimal entry point
  human_chat/
    config.py             # Environment-based settings
    logging_config.py     # Logging setup helpers
    llm.py                # Chat model factory
    schemas.py            # Graph state and structured output schemas
    tts.py                # GPT-SoVITS HTTP client and service helpers
    graph.py              # LangGraph workflow
    cli.py                # Runtime helpers for one-shot and interactive execution
  speech/
    tmp.wav               # Generated speech output
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

## Run

```powershell
python main.py
```

The current entry point starts an interactive chat loop. Type `exit`, `quit`, `q`, or `退出` to stop.

If the TTS service fails, HumanChat will keep the text reply and print a speech error instead of exiting the whole chat loop.

## Next Milestones

1. Add conversation memory and session persistence.
2. Add structured error types around model failures.
3. Add tool-calling nodes to the LangGraph workflow.
4. Add a simple UI after the core runtime is stable.
