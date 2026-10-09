import os
from collections.abc import Iterator
from pathlib import Path

from langchain_core.tools import tool

from human_chat.config import PROJECT_ROOT
from human_chat.logging_config import get_logger


IGNORED_DIRS = {
    ".git", ".idea", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".venv", "venv", "node_modules", "dist", "data",
}
SENSITIVE_FILES = {"mcp_servers.json", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db"}
logger = get_logger(__name__)


def list_project_files(root: Path, limit: int = 100) -> list[str]:
    files = []
    root = root.resolve()
    for path in _iter_project_files(root):
        if len(files) >= limit:
            break
        files.append(path.relative_to(root).as_posix())
    return files


def read_project_file(root: Path, file_path: str, max_chars: int = 8000) -> str:
    path = _resolve_project_path(root, file_path)
    if not path.is_file():
        raise ValueError(f"不是文件：{file_path}")

    try:
        with path.open(encoding="utf-8") as stream:
            content = stream.read(max_chars + 1)
    except UnicodeDecodeError as exc:
        raise ValueError(f"无法读取非 UTF-8 文本文件：{file_path}") from exc

    if len(content) > max_chars:
        return content[:max_chars] + "\n...[内容过长，已截断]"
    return content


def search_project_text(root: Path, query: str, limit: int = 50) -> list[dict]:
    normalized_query = query.strip()
    if not normalized_query:
        return []

    matches = []
    root = root.resolve()
    for path in _iter_project_files(root):
        if len(matches) >= limit:
            break
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        for line_number, line in enumerate(lines, start=1):
            if normalized_query in line:
                matches.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "line": line_number,
                        "text": line.strip(),
                    }
                )
                if len(matches) >= limit:
                    break

    return matches


@tool("list_project_files")
def list_project_files_tool() -> str:
    """List readable files inside the HumanChat project."""
    return _run_project_tool("list_project_files", lambda: "\n".join(list_project_files(PROJECT_ROOT, limit=100)))


@tool("read_project_file")
def read_project_file_tool(path: str) -> str:
    """Read a UTF-8 text file inside the HumanChat project."""
    return _run_project_tool("read_project_file", lambda: read_project_file(PROJECT_ROOT, path))


@tool("search_project_text")
def search_project_text_tool(query: str) -> str:
    """Search for exact text inside UTF-8 files in the HumanChat project."""
    return _run_project_tool("search_project_text", lambda: _format_search_results(query))


def get_project_tools():
    return [
        list_project_files_tool,
        read_project_file_tool,
        search_project_text_tool,
    ]


def _run_project_tool(tool_name: str, callback):
    try:
        return callback()
    except Exception as exc:
        logger.warning("Project tool failed: %s", tool_name, exc_info=True)
        return f"[tool_error] {tool_name}: {exc}"


def _format_search_results(query: str) -> str:
    matches = search_project_text(PROJECT_ROOT, query, limit=50)
    if not matches:
        return "未找到匹配内容。"
    return "\n".join(f"{item['path']}:{item['line']}: {item['text']}" for item in matches)


def _resolve_project_path(root: Path, file_path: str) -> Path:
    root = root.resolve()
    path = (root / file_path).resolve()
    if path != root and root not in path.parents:
        raise ValueError("只能访问项目目录内的文件。")
    if _is_ignored(path.relative_to(root)):
        raise ValueError("不允许通过工具访问密钥、运行数据或内部目录。")
    return path


def _is_ignored(path: Path) -> bool:
    name = path.name.lower()
    return (
        any(part.lower() in IGNORED_DIRS for part in path.parts)
        or (name.startswith(".env") and name != ".env.example")
        or name in SENSITIVE_FILES
        or path.suffix.lower() in SENSITIVE_SUFFIXES
    )


def _iter_project_files(root: Path) -> Iterator[Path]:
    # Prune dependencies and runtime data before traversal; filtering rglob results
    # still walks thousands of irrelevant files and can follow unsafe file links.
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        subdirectories[:] = [
            name for name in subdirectories
            if not _is_ignored((current / name).relative_to(root))
        ]
        for name in filenames:
            try:
                path = _resolve_project_path(root, str(current / name))
            except ValueError:
                continue
            if path.is_file():
                yield path
