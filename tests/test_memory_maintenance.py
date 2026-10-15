from datetime import datetime, timedelta, timezone

import pytest
from langgraph.store.memory import InMemoryStore

from human_chat.memory.models import Assertion, Evidence, Mutation
from human_chat.memory.repository import JsonMemoryRepository, LangGraphMemoryRepository
from human_chat.memory.service import LongTermMemoryService


@pytest.fixture(params=["json", "store"])
def service(request, tmp_path):
    repo = JsonMemoryRepository(tmp_path / "memory.json") if request.param == "json" else LangGraphMemoryRepository(InMemoryStore())
    return LongTermMemoryService(repo, ("users", "default", "memory"))


def operation(name, *, second=0, kind="add", target=None, assertion=None, fields=()):
    return Mutation(id=name, user_id="default", kind=kind, text=name,
        assertion=assertion or Assertion(subject="HumanChat", relation="目标", value="记忆模块", state="进行中", deadline="年底"),
        evidence=[Evidence(message_id=f"source-{name}", user_id="default", session_id="s1",
                           message_time=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=second), quote=name)],
        target_id=target.id if target else None, expected_version=target.version if target else None, fields=list(fields))


def test_retry_and_partial_update_preserve_goal(service):
    seed = operation("seed")
    assert service.apply(seed).outcome == "applied"
    assert service.apply(seed).outcome == "already_applied"
    item = service.load().items[0]
    pause = operation("pause", second=2, kind="update", target=item,
                      assertion=item.assertion.model_copy(update={"value": None, "state": "暂停"}), fields=["state"])
    assert service.apply(pause).outcome == "applied"
    current = service.load().items[0]
    assert current.assertion.value == "记忆模块" and current.assertion.deadline == "年底"
    assert current.assertion.state == "暂停" and len(current.history) == 1
    assert service.apply(pause).outcome == "already_applied"
    assert service.apply(operation("stale", second=3, kind="update", target=item, fields=["state"])).reason == "version_changed"


def test_reaffirmation_blocks_late_older_state_and_foreign_user(service):
    service.apply(operation("seed"))
    item = service.load().items[0]
    assert service.apply(operation("confirm", second=4, kind="duplicate", target=item)).outcome == "applied"
    item = service.load().items[0]
    assert service.apply(operation("late", second=2, kind="update", target=item, fields=["state"])).reason == "older_source"
    foreign = operation("foreign").model_copy(update={"user_id": "other"})
    assert service.apply(foreign).reason == "wrong_user"


def test_correction_and_retraction_do_not_become_valid_history(service):
    service.apply(operation("seed"))
    item = service.load().items[0]
    correction = operation("correct", second=2, kind="correct", target=item,
        assertion=item.assertion.model_copy(update={"deadline": "明年", "scope": "代码审查"}), fields=["deadline", "scope"])
    assert service.apply(correction).outcome == "applied"
    item = service.load().items[0]
    assert item.history[0].status == "revoked" and item.assertion.scope == "代码审查"
    old = operation("old-replay", second=1)
    assert service.apply(old).reason == "revoked_source"
    assert service.apply(operation("withdraw", second=3, kind="retract", target=item)).outcome == "applied"
    assert service.load().items == []
    assert all(version.status == "revoked" for version in service.inventory()[0].history)


def test_delete_removes_content_and_blocks_old_unknown_operations(service):
    seed = operation("seed")
    service.apply(seed)
    item = service.load().items[0]
    service.delete_by_id(item.id)
    tombstone = service.inventory()[0]
    assert tombstone.text == "" and tombstone.assertion is None
    assert tombstone.evidence == [] and tombstone.history == []
    assert service.apply(seed).outcome == "already_applied"
    assert service.apply(operation("unseen-old", second=1)).reason == "deleted_source"
    assert service.load().items == []


def test_persistent_forbid_blocks_fresh_sources_of_same_identity(service):
    service.apply(operation("seed"))
    assert service.apply(operation("ban", second=1, kind="forbid")).outcome == "applied"
    assert service.apply(operation("new-after-ban", second=2)).reason == "forbidden_identity"
    assert service.load().items == []
