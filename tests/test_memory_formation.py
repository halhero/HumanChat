from human_chat.config import Settings
from human_chat.messages import SourceBatch, new_source
from human_chat.memory.formation import Candidate, Extraction, MemoryFormation, Quote, Review, Decision
from human_chat.memory.models import Assertion
from human_chat.memory.repository import JsonMemoryRepository
from human_chat.memory.service import LongTermMemoryService
from human_chat.memory.retrieval import MemoryRetriever


class ScriptedModel:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        return self.schema.model_validate(self.results.pop(0))


def test_untrusted_quote_and_nonasserted_claims_are_not_grounded():
    source = new_source("我喜欢Python", user_id="default", session_id="s1")
    base = Candidate(text="用户喜欢Python", assertion=Assertion(subject="user", relation="喜欢", value="Python"),
                     subject_kind="user", modality="asserted", intent="assert", supplied_fields=["value"],
                     evidence=[Quote(message_id=source.id, quote=source.content)])
    model = ScriptedModel([Extraction(candidates=[
        base, base.model_copy(update={"modality": "hypothetical"}),
        base.model_copy(update={"evidence": [Quote(message_id="invented", quote="Python")]}),
        base.model_copy(update={"subject_kind": "other"}),
    ]).model_dump()])
    service = type("Service", (), {"user_id": "default"})()
    grounded, diagnostics = MemoryFormation(service, model, Settings()).extract(SourceBatch(user_id="default", new_messages=[source]))
    assert len(grounded) == 1 and len(diagnostics) == 3
    assert grounded[0].claim.assertion.subject == "user:default"


def test_review_cannot_patch_unsupported_deadline_and_plan_is_replayable(tmp_path):
    service = LongTermMemoryService(JsonMemoryRepository(tmp_path / "m.json"), ("users", "default", "memory"))
    source = new_source("我的目标是完成HumanChat，年底前完成", user_id="default", session_id="s1")
    candidate = Candidate(text=source.content, assertion=Assertion(subject="HumanChat", relation="目标", value="完成HumanChat", deadline="年底"),
                          subject_kind="project", modality="asserted", intent="assert", supplied_fields=["value", "deadline"],
                          evidence=[Quote(message_id=source.id, quote=source.content)])
    model = ScriptedModel([Extraction(candidates=[candidate]).model_dump(),
                           Review(decisions=[Decision(candidate_id="candidate-0", verdict="supported", reason="明确目标",
                                                      reviewed_assertion=candidate.assertion)]).model_dump()])
    formation = MemoryFormation(service, model, Settings(), MemoryRetriever(service))
    plan = formation.prepare(SourceBatch(user_id="default", new_messages=[source]), "job-1")
    assert len(model.calls) == 2 and len(plan.operations) == 1
    assert service.apply(plan.operations[0]).outcome == "applied"
    assert service.apply(plan.operations[0]).outcome == "already_applied"
    item = service.load().items[0]
    pause = new_source("这个目标暂停", user_id="default", session_id="s1")
    candidate = candidate.model_copy(update={"text": pause.content, "intent": "change", "supplied_fields": ["state"],
        "assertion": item.assertion.model_copy(update={"state": "暂停", "deadline": None}),
        "evidence": [Quote(message_id=pause.id, quote=pause.content)]})
    model = ScriptedModel([Extraction(candidates=[candidate]).model_dump(),
        Review(decisions=[Decision(candidate_id="candidate-0", verdict="supported", operation="update", target_id=item.id,
                                  fields=["state", "deadline"], reason="错误地试图清空期限", reviewed_assertion=candidate.assertion)]).model_dump()])
    plan = MemoryFormation(service, model, Settings(), MemoryRetriever(service)).prepare(
        SourceBatch(user_id="default", new_messages=[pause]), "job-2")
    assert plan.operations == [] and plan.diagnostics[0]["reason"] == "unsupported_patch_fields"
    assert service.load().items[0].assertion.deadline == "年底"


def test_review_preserves_task_scope_and_does_not_promote_receipt_time(tmp_path):
    service = LongTermMemoryService(JsonMemoryRepository(tmp_path / "m.json"), ("users", "default", "memory"))
    source = new_source("以后审查代码先说风险", user_id="default", session_id="s1")
    claim = Candidate(text=source.content,
        assertion=Assertion(subject="user", relation="回答方式", value="先说风险", effective_time=source.occurred_at.isoformat()),
        subject_kind="user", modality="asserted", intent="assert", supplied_fields=["value", "scope"],
        evidence=[Quote(message_id=source.id, quote=source.content)])
    reviewed = claim.assertion.model_copy(update={"scope": "代码审查", "effective_time": None})
    model = ScriptedModel([Extraction(candidates=[claim]).model_dump(), Review(decisions=[Decision(candidate_id="candidate-0",
        verdict="supported", reviewed_assertion=reviewed, reason="保留原话任务限定，来源时间不是事实时间")]).model_dump()])
    plan = MemoryFormation(service, model, Settings(), MemoryRetriever(service)).prepare(
        SourceBatch(user_id="default", new_messages=[source]), "scope-review")
    assert service.apply(plan.operations[0]).outcome == "applied"
    item = service.load().items[0]
    assert item.assertion.scope == "代码审查" and item.assertion.effective_time is None
