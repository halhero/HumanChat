"""Run the HumanChat API with ``python -m human_chat.api``."""

import uvicorn

from human_chat.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "human_chat.api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
