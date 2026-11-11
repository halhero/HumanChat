import json
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import human_chat.graph as graph_module
from human_chat.config import Settings
from human_chat.memory.context import (
    Applicability,
    ContextAssessment,
    MemoryContextBuilder,
)
from human_chat.memory.models import Assertion, MemoryItem, MemoryVersion
from human_chat.memory.repository import SqliteMemoryRepository
from human_chat.memory.request import assemble_request, context_references
from human_chat.memory.retrieval import MemoryRetriever
from human_chat.memory.search import SqliteMemorySearch
from human_chat.memory.service import LongTermMemoryService
from human_chat.memory.tokens import MemoryBudgetError, payload_tokens
from human_chat.tool_provider import ToolRegistry
from tests.test_graph import FakeChatModel
from tests.test_memory_context import AssessmentModel
from tests.test_memory_maintenance import operation, remember


def create_memory(tmp_path):
    search = SqliteMemorySearch(None)
    service = LongTermMemoryService(
        SqliteMemoryRepository(tmp_path / "memory.sqlite"),
        ("users", "default", "memory"),
        search,
    )
    return service, search


class ItemService:
    def __init__(self, item):
        self.item = item

    def get_items(self, item_ids):
        return [self.item] if self.item.id in item_ids else []


def reference(item, selected_id, *, parent_version=None, purpose="guidance"):
    return {
        "bundles": [{
            "id": "selected",
            "critical": False,
            "required": True,
            "entries": [{
                "id": selected_id,
                "parent_id": item.id,
                "parent_version": parent_version or item.version,
                "purpose": purpose,
            }],
        }],
    }


def selected_records(messages):
    return json.loads(messages[0].content.rsplit("\n", 1)[-1])


def test_request_rereads_deletion_and_counts_whole_request(tmp_path):
    service, search = create_memory(tmp_path)
    memory = remember(service, "代码审查先给风险结论")
    model = AssessmentModel(ContextAssessment(task="审查", memories=[Applicability(id=f"{memory.id}@1",
        purpose="guidance", decision="needed", reason="适用")]))
    refs = context_references(MemoryContextBuilder(MemoryRetriever(search), model, Settings()).build("代码审查", []))
    settings = Settings(chat_context_tokens=4000, chat_output_tokens=512)
    messages, _ = assemble_request("system", [], [HumanMessage(content="代码审查")], refs, service, [], settings)
    assert memory.text in messages[0].content
    assert payload_tokens({"messages": [m.model_dump(mode="json") for m in messages], "tools": []}) + 128 <= 3488
    assert service.apply(operation("delete", second=2, kind="delete", target=memory)).outcome == "applied"
    messages, diagnostics = assemble_request("system", [], [HumanMessage(content="代码审查")], refs, service, [], settings)
    assert memory.text not in messages[0].content and "context_changed_since_selection" in diagnostics
    with pytest.raises(MemoryBudgetError):
        assemble_request("system", [], [HumanMessage(content="非常长的问题" * 3000)], {}, service, [], settings)


def test_request_reads_current_and_historical_versions_from_canonical_item():
    observed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ended = observed + timedelta(days=1)
    assertion = Assertion(subject="HumanChat", relation="阶段", value="实现")
    item = MemoryItem(
        id="project",
        user_id="default",
        text="当前阶段：验证",
        version=2,
        assertion=assertion.model_copy(update={"value": "验证"}),
        observed_at=ended,
        history=[MemoryVersion(
            version=1,
            text="当前阶段：实现",
            assertion=assertion,
            observed_at=observed,
            ended_at=ended,
            status="historical",
        )],
    )
    settings = Settings(chat_context_tokens=4000, chat_output_tokens=512)

    current_messages, _ = assemble_request(
        "system", [], [HumanMessage(content="进度")],
        reference(item, "project@2"), ItemService(item), [], settings,
    )
    assert selected_records(current_messages) == [{
        "id": "project@2",
        "parent_id": "project",
        "version": 2,
        "text": "当前阶段：验证",
        "status": "current",
        "assertion": item.assertion.model_dump(mode="json"),
        "observed_at": str(ended),
        "ended_at": None,
        "purpose": "guidance",
    }]

    history_messages, _ = assemble_request(
        "system", [], [HumanMessage(content="之前是什么阶段")],
        reference(item, "project@1", purpose="history"), ItemService(item), [], settings,
    )
    assert selected_records(history_messages)[0] == {
        "id": "project@1",
        "parent_id": "project",
        "version": 1,
        "text": "当前阶段：实现",
        "status": "historical",
        "assertion": assertion.model_dump(mode="json"),
        "observed_at": str(observed),
        "ended_at": str(ended),
        "purpose": "history",
    }


@pytest.mark.parametrize(
    ("selected_id", "parent_version", "item_update", "history_status"),
    [
        ("project#2", 2, {}, "historical"),
        ("project@missing", 2, {}, "historical"),
        ("project@3", 2, {}, "historical"),
        ("project@2", 1, {}, "historical"),
        ("project@2", 2, {"status": "deleted"}, "historical"),
        ("project@2", 2, {"status": "retracted"}, "historical"),
        ("project@2", 2, {"assertion": Assertion(
            subject="HumanChat",
            relation="阶段",
            value="验证",
            expires_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )}, "historical"),
        ("project@1", 2, {}, "revoked"),
    ],
)
def test_request_discards_changed_or_unusable_selected_versions(
    selected_id, parent_version, item_update, history_status,
):
    item = MemoryItem(
        id="project",
        user_id="default",
        text="当前阶段：验证",
        version=2,
        assertion=Assertion(subject="HumanChat", relation="阶段", value="验证"),
        history=[MemoryVersion(
            version=1,
            text="当前阶段：实现",
            status=history_status,
        )],
    ).model_copy(update=item_update)
    purpose = "history" if selected_id == "project@1" else "guidance"

    messages, diagnostics = assemble_request(
        "system", [], [HumanMessage(content="进度")],
        reference(
            item,
            selected_id,
            parent_version=parent_version,
            purpose=purpose,
        ),
        ItemService(item),
        [],
        Settings(chat_context_tokens=4000, chat_output_tokens=512),
    )

    assert item.text not in messages[0].content
    assert diagnostics == ["context_changed_since_selection"]


def test_selected_memory_reaches_real_graph_model_input(monkeypatch, tmp_path):
    service, search = create_memory(tmp_path)
    item = remember(service, "代码审查先给风险结论")
    selector = AssessmentModel(ContextAssessment(task="审查", memories=[Applicability(id=f"{item.id}@1",
        purpose="guidance", decision="needed", reason="适用")]))
    model = FakeChatModel([AIMessage(content="风险结论")])
    monkeypatch.setattr(graph_module, "create_chat_model", lambda settings: model)
    settings = Settings(memory_extraction_enabled=False)
    graph = graph_module.build_graph(settings, memory_service=service, tool_registry=ToolRegistry([]),
        context_builder=MemoryContextBuilder(MemoryRetriever(search), selector, settings))
    assert graph.invoke({"question": "代码审查Python"})["assistant_text"] == "风险结论"
    assert item.text in model.calls[0][0].content
