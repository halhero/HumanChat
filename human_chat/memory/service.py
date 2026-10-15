from threading import RLock
from typing import Protocol
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from human_chat.memory.models import Evidence, LongTermMemory, MemoryItem, MemoryVersion, Mutation, MutationResult
from human_chat.memory.repository import MemoryNamespace, MemoryRepository
from human_chat.messages import new_source


class MemoryService(Protocol):
    @property
    def user_id(self) -> str:
        ...

    def inventory(self) -> list[MemoryItem]:
        ...

    def apply(self, mutation: Mutation) -> MutationResult:
        ...

    def load(self) -> LongTermMemory:
        ...

    def add(
        self,
        text: str,
        source: str = "manual",
        confidence: float | None = None,
    ) -> MemoryItem | None:
        ...

    def delete_by_id(self, item_id: str) -> MemoryItem | None:
        ...

    def format_for_prompt(self) -> str:
        ...


class LongTermMemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        namespace: MemoryNamespace,
    ):
        self._repository = repository
        self._namespace = namespace
        self._lock = RLock()

    @property
    def user_id(self):
        return self._namespace[1]

    def inventory(self):
        with self._lock, self._repository.locked(self._namespace):
            items = self._repository.list_items(self._namespace)
            if any(item.user_id not in (None, self.user_id) for item in items):
                raise ValueError("Stored memory does not belong to its namespace")
            return items

    def load(self) -> LongTermMemory:
        return LongTermMemory(items=[item for item in self.inventory() if item.usable_now()])

    def add(
        self,
        text: str,
        source: str = "manual",
        confidence: float | None = None,
    ) -> MemoryItem | None:
        normalized = text.strip()
        if not normalized:
            return None

        with self._lock, self._repository.locked(self._namespace):
            items = self._repository.list_items(self._namespace)
            if normalized in [item.text for item in items]:
                return None

            item = MemoryItem(
                text=normalized,
                source=source,
                confidence=confidence,
                user_id=self.user_id,
            )
            if source == "manual":
                origin = new_source(normalized, user_id=self.user_id, session_id="manual")
                item.evidence = [Evidence(message_id=origin.id, user_id=self.user_id, session_id="manual",
                                          message_time=origin.occurred_at, quote=normalized)]
                item.observed_at = origin.occurred_at
                item.seen_sources = [origin.id]
            self._repository.upsert_item(self._namespace, item)
        return item

    def delete_by_id(self, item_id: str) -> MemoryItem | None:
        normalized = item_id.strip()
        if not normalized:
            return None

        with self._lock:
            item = self._repository.get_item(self._namespace, normalized)
            if item is None:
                return None
            if not self._repository.delete_item(self._namespace, item.id):
                return None
        return item

    def format_for_prompt(self) -> str:
        items = [item.text for item in self.load().items]
        if not items:
            return "暂无长期记忆。"
        return "\n".join(["长期记忆：", *[f"- {item}" for item in items]])

    def apply(self, mutation: Mutation) -> MutationResult:
        """One logical record, its history and receipt commit in one storage write."""
        def result(outcome, reason="", item_id=None):
            return MutationResult(operation_id=mutation.id, outcome=outcome, reason=reason, item_id=item_id)

        with self._lock, self._repository.locked(self._namespace):
            if mutation.user_id != self.user_id or any(e.user_id != self.user_id for e in mutation.evidence):
                return result("rejected", "wrong_user")
            items = self.inventory()
            for item in items:
                if mutation.id in item.receipts:
                    if item.receipts[mutation.id] != mutation.digest():
                        return result("rejected", "operation_id_reused")
                    return result("already_applied", item_id=item.id)
            sources = {e.message_id for e in mutation.evidence}
            observed = max(e.message_time for e in mutation.evidence)
            if mutation.kind == "add":
                if mutation.target_id is not None or mutation.expected_version is not None:
                    return result("rejected", "add_has_target")
                if mutation.assertion is None or not mutation.assertion.value:
                    return result("rejected", "complete_assertion_required")
                text = mutation.assertion.render()
                if any(item.usable_now() and item.text == text for item in items):
                    return result("conflict", "duplicate_requires_target")
                item = MemoryItem(id=uuid5(NAMESPACE_URL, f"{self._namespace}:{mutation.id}").hex,
                                  user_id=self.user_id, text=text, assertion=mutation.assertion,
                                  source="automatic", evidence=mutation.evidence,
                                  context_message_ids=mutation.context_message_ids,
                                  observed_at=observed, seen_sources=sorted(sources))
            else:
                item = next((item for item in items if item.id == mutation.target_id), None)
                if item is None or item.status != "active":
                    return result("conflict", "target_unavailable")
                if item.version != mutation.expected_version:
                    return result("conflict", "version_changed", item.id)
                if item.observed_at and observed <= item.observed_at:
                    return result("conflict", "older_source", item.id)
                if sources.issubset(item.seen_sources):
                    return result("rejected", "replayed_source", item.id)
                if mutation.kind not in ("update", "duplicate"):
                    return result("rejected", "unsupported_operation", item.id)
                if item.assertion is None or mutation.assertion is None:
                    return result("rejected", "structured_target_required", item.id)
                if item.assertion.identity() != mutation.assertion.identity():
                    return result("rejected", "identity_changed", item.id)
                item = item.model_copy(deep=True)
                if mutation.kind == "duplicate":
                    claimed = mutation.assertion.model_dump(exclude_none=True)
                    if any(item.assertion.model_dump()[key] != value for key, value in claimed.items()):
                        return result("rejected", "duplicate_changes_content", item.id)
                    item.evidence = list({(e.message_id, e.quote): e for e in [*item.evidence, *mutation.evidence]}.values())
                else:
                    if not mutation.fields or "scope" in mutation.fields:
                        return result("rejected", "invalid_update_fields", item.id)
                    if "value" in mutation.fields and not mutation.assertion.value:
                        return result("rejected", "cannot_clear_content", item.id)
                    item.history.append(MemoryVersion(version=item.version, text=item.text,
                        assertion=item.assertion, evidence=item.evidence, observed_at=item.observed_at,
                        ended_at=observed, status="historical"))
                    patch = {field: getattr(mutation.assertion, field) for field in mutation.fields}
                    item.assertion = type(item.assertion).model_validate({**item.assertion.model_dump(), **patch})
                    item.text = item.assertion.render()
                    item.evidence = mutation.evidence
                item.version += 1
                item.observed_at = observed
                item.updated_at = datetime.now(timezone.utc).isoformat()
                item.seen_sources = sorted(set(item.seen_sources) | sources)
                item.context_message_ids = mutation.context_message_ids
            item.receipts[mutation.id] = mutation.digest()
            self._repository.upsert_item(self._namespace, item)
            return result("applied", item_id=item.id)
