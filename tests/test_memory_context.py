from human_chat.config import Settings
from human_chat.memory.context import MemoryContextBuilder, ContextAssessment, Applicability
from human_chat.memory.repository import JsonMemoryRepository
from human_chat.memory.retrieval import MemoryRetriever
from human_chat.memory.service import LongTermMemoryService
from tests.test_memory_maintenance import remember


class AssessmentModel:
    def __init__(self, result):
        self.result = result

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages):
        return self.result


def test_nontopical_task_guidance_is_protected_but_unrelated_memory_is_not(tmp_path):
    service = LongTermMemoryService(JsonMemoryRepository(tmp_path / "m.json"), ("users", "default", "memory"))
    guidance = remember(service, "代码审查时先给风险结论")
    unrelated = remember(service, "家用电脑是Windows")
    result = ContextAssessment(task="代码审查", memories=[
        Applicability(id=f"{guidance.id}@1", purpose="guidance", decision="needed", reason="任务范围适用"),
        Applicability(id=f"{unrelated.id}@1", purpose="background", decision="exclude", reason="不同任务"),
    ])
    context = MemoryContextBuilder(MemoryRetriever(service, limit=1), AssessmentModel(result), Settings()).build("Python", [])
    assert len(context.bundles) == 1 and context.bundles[0].critical
    candidate, _ = context.bundles[0].entries[0]
    assert candidate.view.parent_id == guidance.id and "task_background_catalog" in candidate.reasons
