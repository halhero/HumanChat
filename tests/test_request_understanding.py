import json

import pytest
from pydantic import ValidationError

from human_chat.memory.index_store import MemoryIndexEntry
from human_chat.memory.retrieval import RecallResult, RecalledMemory
from human_chat.request_understanding import (
    MemoryTriageDecision,
    TRIAGE_MEMORY_LIMIT,
    TriageDecision,
    invoke_request_understanding,
)


class StructuredModelStub:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def with_structured_output(self, schema, *, method):
        parent = self

        class StructuredModel:
            def invoke(self, messages):
                parent.calls.append({
                    "schema": schema,
                    "method": method,
                    "messages": messages,
                })
                return parent.results.pop(0)

        return StructuredModel()


class NoRecall:
    def recall(self, *args, **kwargs):
        raise AssertionError("request understanding unexpectedly recalled memory")


def invoke(results, question="当前问题", recent_context=None, retriever=None):
    model = StructuredModelStub(results)
    understanding = invoke_request_understanding(
        model,
        retriever or NoRecall(),
        question,
        recent_context or [],
    )
    return understanding, model


def test_self_contained_request_is_resolved_without_memory_recall():
    understanding, model = invoke([{
        "outcome": "resolved",
        "resolved_request": "解释 Python 装饰器的工作原理。",
        "execution_mode": "react",
    }], question="解释 Python 装饰器的工作原理")

    assert understanding.resolved_request == "解释 Python 装饰器的工作原理。"
    assert understanding.clarification_question == ""
    assert understanding.execution_mode == "react"
    assert len(model.calls) == 1
    assert model.calls[0]["schema"] is TriageDecision
    assert model.calls[0]["method"] == "json_mode"


def test_context_dependent_request_receives_bounded_context_payload():
    context = [
        {"role": "human", "content": "我们采用 SQLite 保存 checkpoint。"},
        {"role": "ai", "content": "下一步可以整理恢复流程。"},
    ]
    understanding, model = invoke(
        [{
            "outcome": "resolved",
            "resolved_request": "继续整理 HumanChat 的 SQLite checkpoint 恢复流程。",
            "execution_mode": "plan_execute",
        }],
        question="继续",
        recent_context=context,
    )

    payload = json.loads(model.calls[0]["messages"][-1].content)
    assert payload == {
        "recent_context": context,
        "current_user_input": "继续",
    }
    assert "SQLite checkpoint" in understanding.resolved_request
    assert understanding.execution_mode == "plan_execute"


def test_material_ambiguity_returns_one_minimal_clarification_question():
    understanding, model = invoke([{
        "outcome": "clarify",
        "clarification_question": "你希望把哪份内容发送给哪位接收者？",
    }], question="把它发给他")

    assert understanding.resolved_request == ""
    assert understanding.clarification_question == "你希望把哪份内容发送给哪位接收者？"
    assert len(model.calls) == 1


def test_memory_search_uses_one_recall_and_a_second_triage_call():
    context = [{"role": "human", "content": "我们之前讨论过 HumanChat。"}]
    entry = MemoryIndexEntry(
        id="project@3",
        parent_id="project",
        parent_version=3,
        version=3,
        text="HumanChat 当前阶段是完善请求理解与长期记忆召回。",
        status="current",
        assertion={"subject": "HumanChat", "relation": "当前阶段"},
        observed_at="2026-11-16T12:00:00+00:00",
    )

    class Retriever:
        def __init__(self):
            self.calls = []

        def recall(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return RecallResult([RecalledMemory(entry, ["semantic"])], [])

    retriever = Retriever()
    understanding, model = invoke(
        [
            {
                "outcome": "search_memory",
                "memory_query": "HumanChat 项目当前阶段和后续目标",
            },
            {
                "outcome": "resolved",
                "resolved_request": "继续完善 HumanChat 的请求理解与长期记忆召回流程。",
                "execution_mode": "plan_execute",
            },
        ],
        question="继续上次的 HumanChat 项目",
        recent_context=context,
        retriever=retriever,
    )

    assert retriever.calls == [(
        "HumanChat 项目当前阶段和后续目标",
        {"purpose": "triage", "limit": TRIAGE_MEMORY_LIMIT},
    )]
    assert [call["schema"] for call in model.calls] == [
        TriageDecision,
        MemoryTriageDecision,
    ]
    second_payload = json.loads(model.calls[1]["messages"][-1].content)
    assert second_payload == {
        "current_user_input": "继续上次的 HumanChat 项目",
        "recent_context": context,
        "memory_candidates": [entry.payload()],
    }
    assert understanding.resolved_request == "继续完善 HumanChat 的请求理解与长期记忆召回流程。"
    assert understanding.execution_mode == "plan_execute"


def test_empty_memory_candidates_can_produce_clarification_without_recall_loop():
    class Retriever:
        def __init__(self):
            self.calls = 0

        def recall(self, query, **kwargs):
            self.calls += 1
            return RecallResult([], [])

    retriever = Retriever()
    understanding, model = invoke(
        [
            {
                "outcome": "search_memory",
                "memory_query": "用户之前讨论的 Atlas 项目",
            },
            {
                "outcome": "clarify",
                "clarification_question": "你指的是哪个 Atlas 项目？",
            },
        ],
        question="继续之前的 Atlas 项目",
        retriever=retriever,
    )

    assert retriever.calls == 1
    assert len(model.calls) == 2
    assert understanding.clarification_question == "你指的是哪个 Atlas 项目？"


@pytest.mark.parametrize(
    "result",
    [
        {
            "outcome": "resolved",
            "resolved_request": "",
            "execution_mode": "react",
        },
        {
            "outcome": "search_memory",
            "memory_query": "",
        },
        {
            "outcome": "clarify",
            "clarification_question": "需要补充信息吗？",
            "resolved_request": "不应同时出现",
        },
        {
            "outcome": "unknown",
            "resolved_request": "无效结果",
        },
    ],
)
def test_invalid_first_triage_result_fails_at_the_boundary(result):
    with pytest.raises(ValidationError):
        invoke([result])


@pytest.mark.parametrize(
    "result",
    [
        {"outcome": "resolved", "resolved_request": "", "execution_mode": "react"},
        {
            "outcome": "resolved",
            "resolved_request": "继续现有方案。",
            "execution_mode": "react",
            "clarification_question": "你指的是哪个方案？",
        },
        {
            "outcome": "resolved",
            "resolved_request": "解释装饰器。",
            "execution_mode": "react",
            "intent": "coding",
        },
    ],
)
def test_invalid_second_triage_result_fails_at_the_boundary(result):
    with pytest.raises(ValidationError):
        invoke(
            [
                {
                    "outcome": "search_memory",
                    "memory_query": "项目背景",
                },
                result,
            ],
            retriever=type(
                "EmptyRetriever",
                (),
                {"recall": lambda self, *args, **kwargs: RecallResult([], [])},
            )(),
        )
