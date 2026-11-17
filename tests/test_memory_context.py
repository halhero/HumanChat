import json

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
        self.calls = []

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
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
    model = AssessmentModel(result)
    question = "继续审查 Python 代码并先给出风险结论"
    recent_context = [{"role": "human", "content": "此前还讨论过 MCP 工具接入"}]
    context = MemoryContextBuilder(
        MemoryRetriever(Search()), model, Settings()
    ).build(question, recent_context)

    assert queries == [question]
    payload = json.loads(model.calls[0][-1].content)
    assert payload["question"] == question
    assert payload["recent_context"] == recent_context
    assert len(context.bundles) == 1 and context.bundles[0].critical
    candidate, _ = context.bundles[0].entries[0]
    assert candidate.entry.parent_id == guidance.parent_id
