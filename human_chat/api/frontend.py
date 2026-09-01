"""Optional same-origin delivery of the compiled browser application."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from human_chat.api.errors import ApiError
from human_chat.logging_config import get_logger


logger = get_logger(__name__)


def install_frontend(app: FastAPI, dist_path: Path) -> None:
    """Register the production SPA route when a complete Vite build exists.

    Development does not require ``web/dist`` because Vite serves the frontend on its
    own port. In production, valid API routes are registered before this catch-all route,
    so the frontend cannot shadow the versioned backend contract.
    """

    root = dist_path.expanduser().resolve()
    index_path = root / "index.html"
    if not index_path.is_file():
        logger.info(
            "Frontend build not found at %s; API-only mode is active",
            root,
        )
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise ApiError(404, "not_found", "接口不存在。")

        candidate = (root / full_path).resolve()
        if not _is_within(candidate, root):
            raise ApiError(404, "not_found", "资源不存在。")

        if full_path and candidate.is_file():
            return FileResponse(
                candidate,
                headers={"Cache-Control": _cache_control(full_path)},
            )

        # Paths with a filename extension are asset requests, not client-side routes.
        if Path(full_path).suffix:
            raise ApiError(404, "not_found", "资源不存在。")

        return FileResponse(
            index_path,
            headers={"Cache-Control": "no-cache"},
        )

    logger.info("Serving compiled frontend from %s", root)


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _cache_control(path: str) -> str:
    if path.startswith("assets/"):
        return "public, max-age=31536000, immutable"
    return "public, max-age=3600"
