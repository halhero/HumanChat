from datetime import datetime, timedelta, timezone

import pytest
from langgraph.store.memory import InMemoryStore

from human_chat.memory.models import Assertion, Evidence, Mutation
from human_chat.memory.repository import (
    LangGraphMemoryRepository,
    SqliteMemoryRepository,
)
from human_chat.memory.search import SqliteMemorySearch
from human_chat.memory.service import LongTermMemoryService


@pytest.fixture(params=["sqlite", "store"])
def service(request, tmp_path):
    repository = (SqliteMemoryRepository(tmp_path / "memory.sqlite")
                  if request.param == "sqlite"
                  else LangGraphMemoryRepository(InMemoryStore()))
    search = SqliteMemorySearch(None)
    instance = LongTermMemoryService(repository, ("users", "default", "memory"), search)
    yield instance
    search.close()
    if isinstance(repository, SqliteMemoryRepository):
        repository.close()


def operation(name, *, second=0, kind="add", target=None, assertion=None, fields=()):
    return Mutation(
        id=name,
        user_id="default",
        kind=kind,
        text=name,
        assertion=assertion or Assertion(
            subject="HumanChat", relation="目标", value="记忆模块", state="进行中", deadline="年底"
        ),
        evidence=[Evidence(
            message_id=f"source-{name}",
            user_id="default",
            session_id="s1",
            message_time=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=second),
            quote=name,
        )],
        target_id=target.id if target else None,
        expected_version=target.version if target else None,
        fields=list(fields),
    )


def remember(service, text):
    """Seed synthetic memory through the same mutation boundary as the worker."""
    result = service.apply(operation(text, assertion=Assertion(
        subject="user:default", relation="背景", value=text,
    )))
    assert result.outcome == "applied"
    return service.get_item(result.item_id)


def test_retry_partial_update_and_stale_version(service):
    seed = operation("seed")
    assert service.apply(seed).outcome == "applied"
    assert service.apply(seed).outcome == "already_applied"
    item = service.get_item(service.apply(seed).item_id)
    pause = operation(
        "pause",
        second=2,
        kind="update",
        target=item,
        assertion=item.assertion.model_copy(update={"value": None, "state": "暂停"}),
        fields=["state"],
    )
    assert service.apply(pause).outcome == "applied"
    current = service.get_item(item.id)
    assert current.assertion.value == "记忆模块" and current.assertion.deadline == "年底"
    assert current.assertion.state == "暂停" and len(current.history) == 1
    assert service.apply(pause).outcome == "already_applied"
    assert service.apply(operation("stale", second=3, kind="update", target=item, fields=["state"])).reason == "version_changed"


def test_reaffirmation_and_foreign_user_boundary(service):
    result = service.apply(operation("seed"))
    item = service.get_item(result.item_id)
    assert service.apply(operation("confirm", second=4, kind="duplicate", target=item)).outcome == "applied"
    current = service.get_item(item.id)
    assert current.version == 2
    foreign = operation("foreign").model_copy(update={"user_id": "other"})
    assert service.apply(foreign).reason == "wrong_user"


def test_correction_and_retraction_preserve_version_history(service):
    result = service.apply(operation("seed"))
    item = service.get_item(result.item_id)
    correction = operation(
        "correct",
        second=2,
        kind="correct",
        target=item,
        assertion=item.assertion.model_copy(update={"deadline": "明年", "scope": "代码审查"}),
        fields=["deadline", "scope"],
    )
    assert service.apply(correction).outcome == "applied"
    item = service.get_item(item.id)
    assert item.history[0].status == "revoked" and item.assertion.scope == "代码审查"
    assert service.apply(operation("withdraw", second=3, kind="retract", target=item)).outcome == "applied"
    item = service.get_item(item.id)
    assert item.status == "retracted"
    assert all(version.status == "revoked" for version in item.history)


def test_delete_creates_idempotent_tombstone_without_scanning_other_items(service):
    result = service.apply(operation("seed"))
    item = service.get_item(result.item_id)
    deletion = operation("delete", second=2, kind="delete", target=item)
    assert service.apply(deletion).outcome == "applied"
    assert service.apply(deletion).outcome == "already_applied"
    tombstone = service.get_item(item.id)
    assert tombstone.status == "deleted" and tombstone.text == ""
    assert tombstone.assertion is None and tombstone.evidence == [] and tombstone.history == []
