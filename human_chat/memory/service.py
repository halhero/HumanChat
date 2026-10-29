"""Application service for bounded memory reads and targeted mutations."""

from datetime import datetime, timezone
from threading import RLock
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from human_chat.memory.models import MemoryItem, MemoryVersion, Mutation, MutationResult
from human_chat.memory.repository import MemoryNamespace, MemoryRepository
from human_chat.memory.search import MemorySearch


class MemoryService(Protocol):
    @property
    def user_id(self) -> str:
        ...

    def get_item(self, item_id: str) -> MemoryItem | None:
        ...

    def get_items(self, item_ids: list[str]) -> list[MemoryItem]:
        ...

    def apply(self, mutation: Mutation) -> MutationResult:
        ...


class LongTermMemoryService:
    def __init__(self, repository: MemoryRepository, namespace: MemoryNamespace, search: MemorySearch):
        self._repository = repository
        self._namespace = namespace
        self._search = search
        self._lock = RLock()

    @property
    def user_id(self) -> str:
        return self._namespace[1]

    def get_item(self, item_id: str) -> MemoryItem | None:
        item = self._repository.get_item(self._namespace, item_id)
        self._validate_ownership([item] if item else [])
        return item

    def get_items(self, item_ids: list[str]) -> list[MemoryItem]:
        items = self._repository.get_items(self._namespace, item_ids)
        self._validate_ownership(items)
        return items

    def _validate_ownership(self, items: list[MemoryItem]) -> None:
        if any(item.user_id not in (None, self.user_id) for item in items):
            raise ValueError("Stored memory does not belong to its namespace")

    def _persist(self, item: MemoryItem) -> None:
        self._repository.upsert_item(self._namespace, item)
        self._search.upsert_item(item)

    def _already_applied(self, item: MemoryItem, operation_id: str) -> bool:
        if item.last_operation_id != operation_id:
            return False
        # A retry also repairs a search projection update interrupted after the canonical commit.
        self._search.upsert_item(item)
        return True

    def _tombstone(self, item: MemoryItem, operation_id: str) -> MemoryItem:
        now = datetime.now(timezone.utc)
        return item.model_copy(update={
            "text": "",
            "assertion": None,
            "evidence": [],
            "history": [],
            "context_message_ids": [],
            "status": "deleted",
            "version": item.version + 1,
            "updated_at": now.isoformat(),
            "last_operation_id": operation_id,
        })

    def apply(self, mutation: Mutation) -> MutationResult:
        """Execute one reviewed decision without scanning unrelated memory records."""

        def result(outcome, reason="", item_id=None):
            return MutationResult(operation_id=mutation.id, outcome=outcome, reason=reason, item_id=item_id)

        if mutation.user_id != self.user_id or any(evidence.user_id != self.user_id for evidence in mutation.evidence):
            return result("rejected", "wrong_user")
        observed = max(evidence.message_time for evidence in mutation.evidence)

        with self._lock:
            if mutation.kind == "add":
                if mutation.target_id is not None or mutation.expected_version is not None:
                    return result("rejected", "add_has_target")
                if mutation.assertion is None or not mutation.assertion.value:
                    return result("rejected", "complete_assertion_required")
                item_id = uuid5(NAMESPACE_URL, f"{self._namespace}:{mutation.id}").hex
                existing = self._repository.get_item(self._namespace, item_id)
                if existing:
                    if self._already_applied(existing, mutation.id):
                        return result("already_applied", item_id=item_id)
                    return result("conflict", "operation_id_conflict", item_id)
                item = MemoryItem(
                    id=item_id,
                    user_id=self.user_id,
                    text=mutation.assertion.render(),
                    assertion=mutation.assertion,
                    source="automatic",
                    evidence=mutation.evidence,
                    context_message_ids=mutation.context_message_ids,
                    observed_at=observed,
                    last_operation_id=mutation.id,
                )
                self._persist(item)
                return result("applied", item_id=item.id)

            if mutation.target_id is None:
                return result("rejected", "target_required")
            item = self._repository.get_item(self._namespace, mutation.target_id)
            if item is None:
                return result("conflict", "target_unavailable")
            self._validate_ownership([item])
            if self._already_applied(item, mutation.id):
                return result("already_applied", item_id=item.id)
            if item.status != "active":
                return result("conflict", "target_unavailable")
            if item.version != mutation.expected_version:
                return result("conflict", "version_changed", item.id)

            if mutation.kind == "delete":
                deleted = self._tombstone(item, mutation.id)
                self._persist(deleted)
                return result("applied", item_id=item.id)

            item = item.model_copy(deep=True)
            if mutation.kind == "duplicate":
                item.evidence = list({
                    (evidence.message_id, evidence.quote): evidence
                    for evidence in [*item.evidence, *mutation.evidence]
                }.values())
            else:
                if mutation.kind != "retract" and (mutation.assertion is None or not mutation.fields):
                    return result("rejected", "update_payload_required", item.id)
                if "value" in mutation.fields and mutation.assertion and not mutation.assertion.value:
                    return result("rejected", "cannot_clear_content", item.id)
                item.history.append(MemoryVersion(
                    version=item.version,
                    text=item.text,
                    assertion=item.assertion,
                    evidence=item.evidence,
                    observed_at=item.observed_at,
                    ended_at=observed,
                    status="historical" if mutation.kind == "update" else "revoked",
                ))
                if mutation.kind == "retract" or (mutation.kind == "correct" and mutation.revoke_history):
                    for version in item.history:
                        version.status = "revoked"
                if mutation.kind == "retract":
                    item.status = "retracted"
                else:
                    patch = {field: getattr(mutation.assertion, field) for field in mutation.fields}
                    item.assertion = type(item.assertion).model_validate({**item.assertion.model_dump(), **patch})
                    item.text = item.assertion.render()
                item.evidence = mutation.evidence

            item.version += 1
            item.observed_at = observed
            item.updated_at = datetime.now(timezone.utc).isoformat()
            item.context_message_ids = mutation.context_message_ids
            item.last_operation_id = mutation.id
            self._persist(item)
            return result("applied", item_id=item.id)
