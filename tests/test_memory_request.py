import pytest
from langchain_core.messages import AIMessage, HumanMessage

import human_chat.graph as graph_module
from human_chat.config import Settings
from human_chat.memory.context import (
    Applicability,
    ContextAssessment,
    MemoryContextBuilder,
)
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
