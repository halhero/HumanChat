import json

import pytest
from pydantic import ValidationError

from human_chat.request_understanding import (
    RequestUnderstanding,
    invoke_request_understanding,
)


class StructuredModelStub:
    def __init__(self, result):
        self.result = result
        self.schema = None
        self.method = None
        self.calls = []

    def with_structured_output(self, schema, *, method):
        self.schema = schema
        self.method = method
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        return self.result


def invoke(result, question="当前问题", recent_context=None):
    model = StructuredModelStub(result)
    understanding = invoke_request_understanding(
        model,
        question,
        recent_context or [],
    )
    return understanding, model


def test_self_contained_request_is_returned_without_clarification():
    understanding, model = invoke(
        {
            "resolved_request": "解释 Python 装饰器的工作原理。",
            "clarification_question": "",
        },
        question="解释 Python 装饰器的工作原理",
    )

    assert understanding.resolved_request == "解释 Python 装饰器的工作原理。"
    assert understanding.clarification_question == ""
    assert model.schema is RequestUnderstanding
    assert model.method == "json_mode"


def test_context_dependent_request_receives_bounded_context_payload():
    context = [
        {"role": "human", "content": "我们采用 SQLite 保存 checkpoint。"},
        {"role": "ai", "content": "下一步可以整理恢复流程。"},
    ]
    understanding, model = invoke(
        {
            "resolved_request": "继续整理 HumanChat 的 SQLite checkpoint 恢复流程。",
            "clarification_question": "",
        },
        question="继续",
        recent_context=context,
    )

    payload = json.loads(model.calls[0][-1].content)
    assert payload == {
        "recent_context": context,
        "current_user_input": "继续",
    }
    assert "SQLite checkpoint" in understanding.resolved_request


def test_material_ambiguity_returns_one_minimal_clarification_question():
    understanding, _ = invoke(
        {
            "resolved_request": "",
            "clarification_question": "你希望把哪份内容发送给哪位接收者？",
        },
        question="把它发给他",
    )

    assert understanding.resolved_request == ""
    assert understanding.clarification_question == "你希望把哪份内容发送给哪位接收者？"


def test_non_material_ambiguity_does_not_force_clarification():
    understanding, _ = invoke(
        {
            "resolved_request": "用简洁的方式解释 Python 装饰器。",
            "clarification_question": "",
        },
        question="简单讲讲 Python 装饰器",
    )

    assert understanding.resolved_request
    assert understanding.clarification_question == ""


@pytest.mark.parametrize(
    "result",
    [
        {"resolved_request": "", "clarification_question": ""},
        {
            "resolved_request": "继续现有方案。",
            "clarification_question": "你指的是哪个方案？",
        },
        {
            "resolved_request": "解释装饰器。",
            "clarification_question": "",
            "intent": "coding",
        },
    ],
)
def test_invalid_structured_result_fails_at_the_boundary(result):
    with pytest.raises(ValidationError):
        invoke(result)
