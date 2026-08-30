"""FastAPI dependencies that expose application-scoped resources."""

from typing import Annotated

from fastapi import Depends, Request

from human_chat.application import HumanChatApplication


def get_human_chat_application(request: Request) -> HumanChatApplication:
    application = getattr(request.app.state, "human_chat_application", None)
    if application is None:
        raise RuntimeError("HumanChat application resources are not ready.")
    return application


HumanChatApplicationDependency = Annotated[
    HumanChatApplication,
    Depends(get_human_chat_application),
]
