from human_chat.memory.index_store import MemoryIndexEntry
from human_chat.memory.retrieval import MemoryRetriever


def document(name, *, identity=None, scope=""):
    return MemoryIndexEntry(
        id=f"{name}@1",
        parent_id=name,
        parent_version=1,
        version=1,
        text=name,
        status="current",
        assertion={"scope": scope} if scope else None,
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

        def search_global_scope(self, limit, statuses):
            return []

        def has_documents(self):
            return True

    result = MemoryRetriever(Search(), limit=2).recall(
        "query", purpose="maintenance", identity=("user", "目标", "")
    )
    candidates = {candidate.entry.parent_id: candidate for candidate in result.candidates}

    assert set(candidates) == {"shared", "lexical", "identity"}
    assert candidates["shared"].reasons == ["lexical", "semantic"]
    assert candidates["identity"].reasons == ["identity"]


def test_answer_recall_reserves_global_scope_and_keeps_final_candidates_bounded():
    global_rule = document("global", scope="全局")
    lexical = [document(f"lexical-{index}") for index in range(8)]

    class Search:
        semantic_enabled = False

        def search_lexical(self, query, limit, statuses):
            return lexical

        def search_semantic(self, query, limit, statuses):
            return []

        def search_identity(self, identity, statuses):
            return []

        def search_global_scope(self, limit, statuses):
            assert limit == 2
            return [global_rule]

        def has_documents(self):
            return True

    result = MemoryRetriever(Search(), limit=6).recall("unrelated question")

    assert len(result.candidates) == 6
    assert result.candidates[0].entry.id == global_rule.id
    assert result.candidates[0].reasons == ["global_scope"]
