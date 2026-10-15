from human_chat.config import Settings
from human_chat.messages import SourceBatch, new_source
from human_chat.memory.formation import Candidate, Extraction, MemoryFormation, Quote
from human_chat.memory.models import Assertion


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
