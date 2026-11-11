"""Hybrid recall over the lightweight search projection."""

from dataclasses import dataclass, field

from human_chat.logging_config import get_logger
from human_chat.memory.index_store import MemoryIndexEntry, MemoryIndexReader

logger = get_logger(__name__)


@dataclass
class RecalledMemory:
    entry: MemoryIndexEntry
    reasons: list[str] = field(default_factory=list)
    score: float = 0

    def payload(self):
        return {**self.entry.payload(), "recall_reasons": self.reasons}


@dataclass
class RecallResult:
    candidates: list[RecalledMemory]
    diagnostics: list[str]


class MemoryRetriever:
    def __init__(self, index_reader: MemoryIndexReader, limit: int = 24):
        self._index_reader = index_reader
        self.limit = limit

    def has_documents(self) -> bool:
        return self._index_reader.has_documents()

    def recall(self, query: str, *, purpose: str = "answer", identity=None) -> RecallResult:
        statuses = ("current", "historical") if purpose == "answer" else (
            "current", "historical", "revoked", "expired"
        )
        ranked: dict[str, RecalledMemory] = {}
        diagnostics: list[str] = []
        window = self.limit * 4

        def enter(document: MemoryIndexEntry, reason: str, rank: int | None = None):
            candidate = ranked.setdefault(document.id, RecalledMemory(document))
            if reason not in candidate.reasons:
                candidate.reasons.append(reason)
            if rank is not None:
                candidate.score += 1 / (60 + rank)

        for rank, document in enumerate(self._index_reader.search_lexical(query, window, statuses), 1):
            enter(document, "lexical", rank)

        if self._index_reader.semantic_enabled:
            try:
                for rank, document in enumerate(self._index_reader.search_semantic(query, window, statuses), 1):
                    enter(document, "semantic", rank)
            except Exception:
                logger.exception("Semantic memory recall unavailable")
                diagnostics.append("semantic_unavailable")
        elif self._index_reader.has_documents():
            diagnostics.append("semantic_not_configured")

        hybrid = sorted(ranked.values(), key=lambda candidate: candidate.score, reverse=True)[:self.limit]
        ranked = {candidate.entry.id: candidate for candidate in hybrid}
        if purpose == "answer":
            for document in self._index_reader.current_documents():
                enter(document, "task_background_catalog")
        if identity:
            for document in self._index_reader.search_identity(identity, statuses):
                enter(document, "identity")
        return RecallResult(list(ranked.values()), diagnostics)
