"""Purpose-aware candidate entry, before any applicability judgment."""

import re
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from human_chat.logging_config import get_logger
from human_chat.memory.index import MemoryView, memory_views


logger = get_logger(__name__)


def lexical_tokens(text):
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.casefold())
    tokens = []
    for word in words:
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            tokens.extend(word)
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
        else:
            tokens.append(word)
    return tokens


@dataclass
class RecalledMemory:
    view: MemoryView
    reasons: list[str] = field(default_factory=list)
    score: float = 0

    def payload(self):
        return {**self.view.payload(), "recall_reasons": self.reasons}


@dataclass
class RecallResult:
    candidates: list[RecalledMemory]
    diagnostics: list[str]


class MemoryRetriever:
    def __init__(self, service, index=None, limit=12):
        self.service, self.index, self.limit = service, index, limit

    def recall(self, query, *, purpose="answer", identity=None):
        views = memory_views(self.service.inventory())
        if purpose == "answer":
            views = [v for v in views if v.status in ("current", "historical")]
        by_id = {v.id: v for v in views}
        ranked, diagnostics = {}, []

        def enter(view, reason, rank=None):
            candidate = ranked.setdefault(view.id, RecalledMemory(view))
            if reason not in candidate.reasons:
                candidate.reasons.append(reason)
            if rank is not None:
                candidate.score += 1 / (60 + rank)

        tokens = lexical_tokens(query)
        corpus = [lexical_tokens(v.text) for v in views]
        if tokens and corpus and any(corpus):
            scores = BM25Okapi(corpus).get_scores(tokens)
            matches = [i for i in range(len(views)) if set(tokens).intersection(corpus[i])]
            for rank, i in enumerate(sorted(matches, key=lambda i: scores[i], reverse=True)[:self.limit], 1):
                enter(views[i], "lexical", rank)
        if self.index and views:
            try:
                for rank, (item_id, _, parent_version) in enumerate(self.index.search(query, self.limit), 1):
                    view = by_id.get(item_id)
                    if view and view.parent_version == parent_version:
                        enter(view, "semantic", rank)
                    else:
                        diagnostics.append("semantic_projection_stale")
            except Exception:
                # External embedding service is a boundary; do not silently call this a complete recall.
                logger.exception("Semantic memory recall unavailable")
                diagnostics.append("semantic_unavailable")
        elif views:
            diagnostics.append("semantic_not_configured")

        hybrid = sorted(ranked.values(), key=lambda c: c.score, reverse=True)[:self.limit]
        ranked = {c.view.id: c for c in hybrid}
        for view in views:
            if identity and view.assertion and view.assertion.identity() == identity:
                enter(view, "identity")
            if purpose == "answer" and view.status == "current":
                # A separate catalog lane, not constrained by topical top-k. Step 11 judges usefulness.
                enter(view, "task_background_catalog")
        return RecallResult(list(ranked.values()), list(dict.fromkeys(diagnostics)))
