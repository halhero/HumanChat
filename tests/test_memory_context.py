from human_chat.config import Settings
from human_chat.memory.context import (
    Applicability,
    ContextAssessment,
    MemoryContextBuilder,
)
from human_chat.memory.index_store import MemoryIndexEntry
from human_chat.memory.retrieval import MemoryRetriever


class AssessmentModel:
    def __init__(self, result):
        self.result = result

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages):
        return self.result


def test_applicability_keeps_needed_guidance_and_excludes_unrelated_candidate():
    guidance = MemoryIndexEntry("guidance@1", "guidance", 1, 1, "代码审查先给风险", "current", None, None)
    unrelated = MemoryIndexEntry("unrelated@1", "unrelated", 1, 1, "家用电脑是Windows", "current", None, None)

    queries = []

    class Search:
        semantic_enabled = False

        def search_lexical(self, query, limit, statuses):
            queries.append(query)
            return [guidance, unrelated]

        def search_semantic(self, query, limit, statuses):
            return []

        def search_identity(self, identity, statuses):
            return []

        def search_global_scope(self, limit, statuses):
            return []

        def has_documents(self):
            return True

    result = ContextAssessment(task="代码审查", memories=[
        Applicability(id=guidance.id, purpose="guidance", decision="needed", reason="任务范围适用"),
        Applicability(id=unrelated.id, purpose="background", decision="exclude", reason="不同任务"),
    ])
    context = MemoryContextBuilder(
        MemoryRetriever(Search()), AssessmentModel(result), Settings()
    ).build("继续", [{"role": "human", "content": "Python代码审查"}])

    assert "Python代码审查" in queries[0] and "继续" in queries[0]
    assert len(context.bundles) == 1 and context.bundles[0].critical
    candidate, _ = context.bundles[0].entries[0]
    assert candidate.entry.parent_id == guidance.parent_id
