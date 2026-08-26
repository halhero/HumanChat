"""Stable API error types and FastAPI exception handlers."""

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from human_chat.logging_config import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True)
class ApiError(Exception):
    status_code: int
    code: str
    message: str


def install_exception_handlers(app: FastAPI) -> None:
    """Return one public error envelope without leaking internal exceptions."""

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        return _error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ):
        logger.info(
            "Request validation failed: %s",
            exc.errors(),
            extra={"request_id": _request_id(request)},
        )
        return _error_response(
            request,
            status_code=422,
            code="validation_error",
            message="请求参数格式不正确。",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        logger.exception(
            "Unhandled API error",
            extra={"request_id": _request_id(request)},
        )
        return _error_response(
            request,
            status_code=500,
            code="internal_error",
            message="服务暂时无法完成请求。",
        )


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id},
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")
