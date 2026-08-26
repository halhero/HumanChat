"""FastAPI dependencies that expose application-scoped resources."""

from typing import Annotated

from fastapi import Depends, Request

from human_chat.runtime import ChatApplication


def get_chat_application(request: Request) -> ChatApplication:
    application = getattr(request.app.state, "chat_application", None)
    if application is None:
        raise RuntimeError("HumanChat application resources are not ready.")
    return application


ChatApplicationDependency = Annotated[
    ChatApplication,
    Depends(get_chat_application),
]
