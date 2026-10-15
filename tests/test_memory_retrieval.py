from human_chat.memory.retrieval import MemoryRetriever
from human_chat.memory.repository import JsonMemoryRepository
from human_chat.memory.service import LongTermMemoryService


def test_task_catalog_survives_topical_top_k_and_stale_vectors(tmp_path):
    service = LongTermMemoryService(JsonMemoryRepository(tmp_path / "m.json"), ("users", "default", "memory"))
    guidance = service.add("代码审查时先给出风险结论")
    topic = service.add("Python Python Python")

    class StaleIndex:
        def covers(self, views):
            return False

        def search(self, query, limit):
            return [(f"{guidance.id}@1", 0.99, 99)]

    retriever = MemoryRetriever(service, StaleIndex(), limit=1)
    result = retriever.recall("Python")
    candidates = {c.view.parent_id: c for c in result.candidates}
    assert "task_background_catalog" in candidates[guidance.id].reasons
    assert "semantic" not in candidates[guidance.id].reasons
    assert "lexical" in candidates[topic.id].reasons
    assert result.diagnostics == ["semantic_projection_incomplete", "semantic_projection_stale"]
    service.delete_by_id(guidance.id)
    assert all(c.view.parent_id != guidance.id for c in retriever.recall("代码审查").candidates)
