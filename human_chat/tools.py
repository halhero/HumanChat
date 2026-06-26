from pathlib import Path

from langchain_core.tools import tool

from human_chat.config import PROJECT_ROOT


IGNORED_DIRS = {".git", ".idea", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}


def list_project_files(root: Path, limit: int = 100) -> list[str]:
    files = []
    for path in root.rglob("*"):
        if len(files) >= limit:
            break
        if not path.is_file() or _is_ignored(path):
            continue
        files.append(path.relative_to(root).as_posix())
    return files


def read_project_file(root: Path, file_path: str, max_chars: int = 8000) -> str:
    path = _resolve_project_path(root, file_path)
    if not path.is_file():
        raise ValueError(f"不是文件：{file_path}")

    try:
        content = path.read_text(encoding="utf-8")
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
    for path in root.rglob("*"):
        if len(matches) >= limit:
            break
        if not path.is_file() or _is_ignored(path):
            continue
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


@tool
def list_project_files_tool() -> str:
    """List readable files inside the HumanChat project."""
    return "\n".join(list_project_files(PROJECT_ROOT, limit=100))


@tool
def read_project_file_tool(path: str) -> str:
    """Read a UTF-8 text file inside the HumanChat project."""
    return read_project_file(PROJECT_ROOT, path)


@tool
def search_project_text_tool(query: str) -> str:
    """Search for exact text inside UTF-8 files in the HumanChat project."""
    matches = search_project_text(PROJECT_ROOT, query, limit=50)
    if not matches:
        return "未找到匹配内容。"
    return "\n".join(f"{item['path']}:{item['line']}: {item['text']}" for item in matches)


def get_project_tools():
    return [
        list_project_files_tool,
        read_project_file_tool,
        search_project_text_tool,
    ]


def _resolve_project_path(root: Path, file_path: str) -> Path:
    root = root.resolve()
    path = (root / file_path).resolve()
    if path != root and root not in path.parents:
        raise ValueError("只能访问项目目录内的文件。")
    return path


def _is_ignored(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path.parts)
