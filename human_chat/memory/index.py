"""Rebuildable semantic projection. Canonical records remain in the repository."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import RLock

from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings


@dataclass(frozen=True)
class MemoryView:
    id: str
    parent_id: str
    parent_version: int
    version: int
    text: str
    status: str
    assertion: object
    evidence: list
    observed_at: object
    ended_at: object = None

    def payload(self):
        return {"id": self.id, "parent_id": self.parent_id, "version": self.version,
                "text": self.text, "status": self.status,
                "assertion": self.assertion.model_dump(mode="json") if self.assertion else None,
                "observed_at": str(self.observed_at) if self.observed_at else None,
                "ended_at": str(self.ended_at) if self.ended_at else None}


def memory_views(items):
    views = []
    for item in items:
        if item.status == "deleted":
            continue
        status = "current" if item.usable_now() else "revoked" if item.status == "retracted" else "expired"
        views.append(MemoryView(f"{item.id}@{item.version}", item.id, item.version, item.version,
                                item.text, status, item.assertion, item.evidence, item.observed_at))
        views.extend(MemoryView(f"{item.id}@{v.version}", item.id, item.version, v.version,
                                v.text, v.status, v.assertion, v.evidence, v.observed_at, v.ended_at)
                     for v in item.history)
    return views


def create_embeddings(settings):
    # Bailian accepts at most ten v4 texts per request. Send strings, not OpenAI token arrays.
    return OpenAIEmbeddings(model=settings.memory_embedding_model,
                            base_url=settings.memory_embedding_base_url or settings.llm_base_url,
                            api_key=settings.memory_embedding_api_key or settings.openai_api_key,
                            check_embedding_ctx_length=False, chunk_size=10, max_retries=1,
                            request_timeout=settings.llm_timeout_seconds,
                            model_kwargs={"encoding_format": "float"})


class MemoryIndex:
    def __init__(self, embeddings, path: Path | None, configuration: str):
        self.embeddings, self.path = embeddings, path
        self.configuration = sha256(configuration.encode()).hexdigest()
        self._lock, self._generation = RLock(), 0
        self.store = (InMemoryVectorStore.load(str(path), embeddings)
                      if path and path.exists() else InMemoryVectorStore(embeddings))
        if any(row["metadata"]["configuration"] != self.configuration for row in self.store.store.values()):
            # The projection can be rebuilt; vectors from different models cannot be mixed.
            self.store.store.clear()
            self._dump()

    def _dump(self):
        if self.path is not None:
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            try:
                self.store.dump(str(temporary))
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def changed(self, item):
        with self._lock:
            self._generation += 1
            if item.status == "deleted":
                self.store.store = {key: row for key, row in self.store.store.items()
                                    if row["metadata"]["parent_id"] != item.id}
                self._dump()

    def sync(self, service):
        with self._lock:
            generation = self._generation
            cached = {row["text"]: row["vector"] for row in self.store.store.values()}
        views = memory_views(service.inventory())
        missing = list(dict.fromkeys(v.text for v in views if v.text not in cached))
        if missing:
            vectors = self.embeddings.embed_documents(missing)
            if len(vectors) != len(missing):
                raise ValueError("Embedding response count does not match input")
            cached.update(zip(missing, vectors))
        rows = {v.id: {"id": v.id, "vector": cached[v.text], "text": v.text,
                      "metadata": {"parent_id": v.parent_id, "parent_version": v.parent_version,
                                   "configuration": self.configuration}}
                for v in views}
        with self._lock:
            if generation != self._generation:
                return False
            self.store.store = rows
            self._dump()
            return True

    def search(self, query, limit=12):
        vector = self.embeddings.embed_query(query)
        with self._lock:
            return [(document.id, score, document.metadata["parent_version"])
                    for document, score in self.store.similarity_search_with_score_by_vector(vector, k=limit)]
