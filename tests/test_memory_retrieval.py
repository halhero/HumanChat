from human_chat.memory.retrieval import MemoryRetriever
from human_chat.memory.search import SearchDocument


def document(name, *, identity=None):
    return SearchDocument(
        id=f"{name}@1",
        parent_id=name,
        parent_version=1,
        version=1,
        text=name,
        status="current",
        assertion=None,
        observed_at=None,
        identity_key=identity,
    )


def test_hybrid_recall_merges_bm25_vectors_and_exact_identity():
    lexical_only = document("lexical")
    shared = document("shared")
    semantic_only = document("semantic")
    exact = document("identity")

    class Search:
        semantic_enabled = True

        def search_lexical(self, query, limit, statuses):
            return [lexical_only, shared]

        def search_semantic(self, query, limit, statuses):
            return [shared, semantic_only]

        def search_identity(self, identity, statuses):
            return [exact]

        def current_documents(self):
            return []

        def has_documents(self):
            return True

    result = MemoryRetriever(Search(), limit=2).recall(
        "query", purpose="maintenance", identity=("user", "目标", "")
    )
    candidates = {candidate.view.parent_id: candidate for candidate in result.candidates}

    assert set(candidates) == {"shared", "lexical", "identity"}
    assert candidates["shared"].reasons == ["lexical", "semantic"]
    assert candidates["identity"].reasons == ["identity"]
