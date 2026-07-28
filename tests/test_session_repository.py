import json

import pytest

from human_chat.session_models import SessionRecord, now_local
from human_chat.storage.json_session_repository import JsonSessionRepository


def test_create_uses_unique_ids_and_persists_records(tmp_path):
    repository = JsonSessionRepository(tmp_path)

    first = repository.create()
    second = repository.create()

    assert first.id != second.id
    assert first.thread_id == first.id
    assert repository.load(first.id) == first


def test_save_does_not_mutate_caller_record(tmp_path):
    repository = JsonSessionRepository(tmp_path)
    session = SessionRecord.create()
    updated = session.model_copy(
        update={"message_count": 4, "updated_at": now_local()}
    )

    repository.save(updated)

    assert session.message_count == 0
    assert repository.load(updated.id).message_count == 4


def test_load_migrates_legacy_session_metadata(tmp_path):
    legacy_id = "20260728_120000"
    (tmp_path / f"{legacy_id}.json").write_text(
        json.dumps(
            {
                "id": legacy_id,
                "created_at": "2026-07-28T12:00:00+08:00",
                "updated_at": "2026-07-28T12:01:00+08:00",
                "message_count": 2,
            }
        ),
        encoding="utf-8",
    )
    repository = JsonSessionRepository(tmp_path)

    session = repository.load(legacy_id)

    assert session.thread_id == legacy_id
    assert session.checkpoint_backend == "legacy"
    assert session.recoverable


def test_load_rejects_path_traversal(tmp_path):
    repository = JsonSessionRepository(tmp_path)

    with pytest.raises(ValueError):
        repository.load("../outside")
