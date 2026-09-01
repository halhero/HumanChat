"""FastAPI application factory and process lifespan management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from human_chat import __version__
from human_chat.application import open_human_chat_application
from human_chat.api.errors import install_exception_handlers
from human_chat.api.frontend import install_frontend
from human_chat.api.routes.health import router as health_router
from human_chat.api.routes.sessions import router as sessions_router
from human_chat.api.routes.turns import router as turns_router
from human_chat.api.routes.voice import router as voice_router
from human_chat.config import Settings, load_settings
from human_chat.conversation import ConversationService
from human_chat.logging_config import setup_logging


def create_api(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        setup_logging()
        with open_human_chat_application(active_settings) as human_chat:
            application.state.human_chat_application = human_chat
            conversations = ConversationService(human_chat)
            application.state.conversation_service = conversations
            try:
                yield
            finally:
                await conversations.shutdown()

    application = FastAPI(
        title="HumanChat API",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = active_settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.api_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Turn-ID"],
    )

    @application.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id[:128]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    install_exception_handlers(application)
    application.include_router(health_router, prefix="/api/v1")
    application.include_router(sessions_router, prefix="/api/v1")
    application.include_router(turns_router, prefix="/api/v1")
    application.include_router(voice_router, prefix="/api/v1")
    install_frontend(application, active_settings.frontend_dist_path)
    return application


app = create_api()
