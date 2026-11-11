from langchain_core.embeddings import Embeddings

from human_chat.memory.index_store import SqliteMemoryIndexStore
from human_chat.memory.models import Assertion
from human_chat.memory.repository import SqliteMemoryRepository
from human_chat.memory.service import LongTermMemoryService
from tests.test_memory_maintenance import operation, remember


class KeywordEmbeddings(Embeddings):
    def __init__(self):
        self.document_batches = []

    @staticmethod
    def vector(text):
        lowered = text.casefold()
        return [float("python" in lowered), float("rust" in lowered), 1.0]

    def embed_documents(self, texts):
        self.document_batches.append(list(texts))
        return [self.vector(text) for text in texts]

    def embed_query(self, text):
        return self.vector(text)


def test_search_projection_updates_one_parent_and_survives_reopen(tmp_path):
    repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    embeddings = KeywordEmbeddings()
    path = tmp_path / "memory.search.sqlite"
    search = SqliteMemoryIndexStore(path, embeddings, "test-model")
    service = LongTermMemoryService(repository, ("users", "default", "memory"), search)

    python = remember(service, "Python项目使用FastAPI")
    rust = remember(service, "Rust项目使用Axum")
    global_result = service.apply(operation(
        "global-guidance",
        second=1,
        assertion=Assertion(
            subject="user:default",
            relation="回答方式",
            value="先给结论",
            scope="全局",
        ),
    ))
    global_rule = service.get_item(global_result.item_id)
    assert len(embeddings.document_batches) == 3
    assert search.search_lexical("FastAPI", 10, ("current",))[0].parent_id == python.id
    assert search.search_semantic("Python", 10, ("current",))[0].parent_id == python.id
    assert search.search_global_scope(10, ("current",))[0].parent_id == global_rule.id

    assert service.apply(operation("delete-rust", second=2, kind="delete", target=rust)).outcome == "applied"
    assert all(document.parent_id != rust.id for document in search.search_lexical("Rust", 10, ("current",)))
    search.close()

    reopened = SqliteMemoryIndexStore(path, embeddings, "test-model")
    assert reopened.search_semantic("Python", 10, ("current",))[0].parent_id == python.id
    assert reopened.search_global_scope(10, ("current",))[0].parent_id == global_rule.id
    reopened.close()
    repository.close()
