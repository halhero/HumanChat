from langchain_core.embeddings import Embeddings

from human_chat.memory.index import MemoryIndex, memory_views
from human_chat.memory.repository import JsonMemoryRepository
from human_chat.memory.service import LongTermMemoryService
from tests.test_memory_maintenance import operation, remember


class CountingEmbeddings(Embeddings):
    """Deterministic adapter test double, not a semantic accuracy model."""
    def __init__(self):
        self.calls = 0
        self.during_embed = None

    def embed_documents(self, texts):
        self.calls += 1
        if self.during_embed:
            self.during_embed()
        return [[len(text), 1.0] for text in texts]

    def embed_query(self, text):
        return [len(text), 1.0]


def test_snapshot_reuse_and_delete_during_slow_sync(tmp_path):
    service = LongTermMemoryService(JsonMemoryRepository(tmp_path / "memory.json"), ("users", "default", "memory"))
    embeddings = CountingEmbeddings()
    path = tmp_path / "index.json"
    index = MemoryIndex(embeddings, path, "test")
    service.attach_index(index)
    first = remember(service, "Only local synthetic content")
    assert not index.covers(memory_views(service.inventory()))
    assert index.sync(service)
    assert index.covers(memory_views(service.inventory()))
    reloaded = MemoryIndex(embeddings, path, "test")
    assert reloaded.sync(service) and embeddings.calls == 1
    second = remember(service, "Must not survive deletion")

    def delete_during_embed():
        assert service.apply(operation("delete", second=2, kind="delete", target=second)).outcome == "applied"

    embeddings.during_embed = delete_during_embed
    assert not index.sync(service)
    assert second.text not in path.read_text(encoding="utf-8")
    embeddings.during_embed = None
    assert index.sync(service)
    assert index.search(first.text)[0][0] == f"{first.id}@1"
