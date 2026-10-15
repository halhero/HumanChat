from threading import RLock
from typing import Protocol
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from human_chat.memory.models import Evidence, LongTermMemory, MemoryItem, MemoryVersion, Mutation, MutationResult, fingerprints
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


class LongTermMemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        namespace: MemoryNamespace,
    ):
        self._repository = repository
        self._namespace = namespace
        self._lock = RLock()
        self._index = None
        self._on_delete = None

    def attach_index(self, index):
        self._index = index

    def on_delete(self, callback):
        self._on_delete = callback

    def _persist(self, item):
        self._repository.upsert_item(self._namespace, item)
        if self._index is not None:
            self._index.changed(item)
        if item.status == "deleted" and self._on_delete is not None:
            self._on_delete(item)

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
            self._persist(item)
        return item

    def delete_by_id(self, item_id: str) -> MemoryItem | None:
        normalized = item_id.strip()
        if not normalized:
            return None

        with self._lock, self._repository.locked(self._namespace):
            item = self._repository.get_item(self._namespace, normalized)
            if item is None or item.status == "deleted":
                return None
            if item.user_id not in (None, self.user_id):
                raise ValueError("Memory belongs to another user")
            self._persist(self._tombstone(item))
        return item

    def _tombstone(self, item):
        hashes = set(item.suppressed) | fingerprints(item.text, item.assertion)
        for version in item.history:
            hashes.update(fingerprints(version.text, version.assertion))
        return item.model_copy(update={
            "text": "", "assertion": None, "evidence": [], "history": [], "context_message_ids": [],
            "status": "deleted", "version": item.version + 1,
            "suppressed": sorted(hashes), "deleted_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

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
            for stored in items:
                if stored.status == "deleted" and sources.intersection(stored.seen_sources) and mutation.kind not in ("delete", "forbid"):
                    return result("rejected", "deleted_source")
                if mutation.assertion and stored.blocked_identity == list(mutation.assertion.identity()) and mutation.kind not in ("delete", "forbid"):
                    return result("rejected", "forbidden_identity")
                if (mutation.kind != "forbid" and stored.status == "deleted" and fingerprints(mutation.text, mutation.assertion).intersection(stored.suppressed)
                        and (sources.intersection(stored.seen_sources) or (stored.deleted_at and observed <= stored.deleted_at))):
                    return result("rejected", "deleted_source")
                if mutation.kind == "add":
                    for version in stored.history:
                        if (version.status == "revoked"
                                and fingerprints(mutation.text, mutation.assertion).intersection(fingerprints(version.text, version.assertion))
                                and (sources.intersection(e.message_id for e in version.evidence)
                                     or (version.ended_at and observed <= version.ended_at))):
                            return result("rejected", "revoked_source")
            if mutation.kind == "forbid":
                if mutation.assertion is None:
                    return result("rejected", "policy_identity_required")
                identity = list(mutation.assertion.identity())
                for stored in items:
                    if stored.assertion and list(stored.assertion.identity()) == identity:
                        tombstone = self._tombstone(stored)
                        tombstone.blocked_identity = identity
                        self._persist(tombstone)
                item = MemoryItem(id=uuid5(NAMESPACE_URL, f"{self._namespace}:{mutation.id}").hex,
                                  text="", user_id=self.user_id, status="deleted", blocked_identity=identity,
                                  seen_sources=sorted(sources), deleted_at=datetime.now(timezone.utc))
            elif mutation.kind == "add":
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
                if mutation.kind == "delete":
                    deleted = self._tombstone(item)
                    deleted.seen_sources = sorted(set(deleted.seen_sources) | sources)
                    deleted.receipts[mutation.id] = mutation.digest()
                    self._persist(deleted)
                    return result("applied", item_id=item.id)
                if mutation.kind != "retract":
                    if (item.assertion is None) != (mutation.assertion is None):
                        return result("rejected", "incompatible_assertion", item.id)
                    if item.assertion:
                        old_identity, new_identity = item.assertion.identity(), mutation.assertion.identity()
                        if old_identity[:2] != new_identity[:2] or (
                            old_identity[2] != new_identity[2] and not (mutation.kind == "correct" and "scope" in mutation.fields)
                        ):
                            return result("rejected", "identity_changed", item.id)
                item = item.model_copy(deep=True)
                if mutation.kind == "duplicate":
                    changed = (any(item.assertion.model_dump()[key] != value
                                   for key, value in mutation.assertion.model_dump(exclude_none=True).items())
                               if item.assertion else item.text != mutation.text)
                    if changed:
                        return result("rejected", "duplicate_changes_content", item.id)
                    item.evidence = list({(e.message_id, e.quote): e for e in [*item.evidence, *mutation.evidence]}.values())
                else:
                    if mutation.kind != "retract" and (not mutation.fields or ("scope" in mutation.fields and mutation.kind != "correct")):
                        return result("rejected", "invalid_update_fields", item.id)
                    if "value" in mutation.fields and item.assertion and not mutation.assertion.value:
                        return result("rejected", "cannot_clear_content", item.id)
                    if item.assertion is None and mutation.kind != "retract" and (
                        mutation.kind != "correct" or mutation.fields != ["value"] or not mutation.text.strip()
                    ):
                        return result("rejected", "legacy_requires_full_correction", item.id)
                    item.history.append(MemoryVersion(version=item.version, text=item.text,
                        assertion=item.assertion, evidence=item.evidence, observed_at=item.observed_at,
                        ended_at=observed, status="historical" if mutation.kind == "update" else "revoked"))
                    if mutation.kind == "retract" or (mutation.kind == "correct" and mutation.revoke_history):
                        for version in item.history:
                            version.status = "revoked"
                    if mutation.kind == "retract":
                        item.status = "retracted"
                    elif item.assertion:
                        patch = {field: getattr(mutation.assertion, field) for field in mutation.fields}
                        item.assertion = type(item.assertion).model_validate({**item.assertion.model_dump(), **patch})
                        item.text = item.assertion.render()
                    else:
                        item.text = mutation.text.strip()
                    item.evidence = mutation.evidence
                item.version += 1
                item.observed_at = observed
                item.updated_at = datetime.now(timezone.utc).isoformat()
                item.seen_sources = sorted(set(item.seen_sources) | sources)
                item.context_message_ids = mutation.context_message_ids
            item.receipts[mutation.id] = mutation.digest()
            self._persist(item)
            return result("applied", item_id=item.id)
