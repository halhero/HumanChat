from pathlib import Path

import pytest

from human_chat.tools import list_project_files, read_project_file, search_project_text


def test_read_project_file_reads_utf8_text(tmp_path: Path):
    target = tmp_path / "demo.txt"
    target.write_text("hello\nHumanChat", encoding="utf-8")

    assert read_project_file(tmp_path, "demo.txt") == "hello\nHumanChat"


def test_read_project_file_blocks_outside_paths(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError):
        read_project_file(tmp_path, "../outside.txt")


def test_search_project_text_returns_matches(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("alpha\nneedle here\n", encoding="utf-8")

    matches = search_project_text(tmp_path, "needle")

    assert matches == [
        {
            "path": "a.py",
            "line": 2,
            "text": "needle here",
        }
    ]


def test_list_project_files_skips_ignored_dirs(tmp_path: Path):
    visible = tmp_path / "visible.txt"
    visible.write_text("ok", encoding="utf-8")
    ignored_dir = tmp_path / "__pycache__"
    ignored_dir.mkdir()
    (ignored_dir / "hidden.pyc").write_text("no", encoding="utf-8")

    files = list_project_files(tmp_path)

    assert "visible.txt" in files
    assert "__pycache__/hidden.pyc" not in files
