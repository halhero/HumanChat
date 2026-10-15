from contextlib import nullcontext

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import START, StateGraph
from langgraph.types import interrupt

import human_chat.application as application_module
import human_chat.graph as graph_module
from human_chat.api.app import create_api
from human_chat.application import open_human_chat_application
from human_chat.checkpointing import open_checkpointer
from human_chat.config import Settings
from human_chat.messages import new_source
from human_chat.schemas import ChatState
from human_chat.storage.json_session_repository import JsonSessionRepository
from human_chat.tool_provider import RegisteredTool, ToolPolicy, ToolRegistry
from tests.test_graph import FakeChatModel


def isolated_settings(tmp_path):
    return Settings(openai_api_key="isolated-placeholder", memory_semantic_enabled=False,
        memory_extraction_enabled=False, session_dir=tmp_path / "sessions", memory_path=tmp_path / "memory.json",
        checkpoint_path=tmp_path / "checkpoint.sqlite")


def test_http_tool_review_restart_and_memory_delete_contract(monkeypatch, tmp_path):
    calls = []

    @tool
    def protected_action(value: str) -> str:
        """Execute an isolated test action."""
        calls.append(value)
        return "done"

    registry = ToolRegistry([RegisteredTool(protected_action, "isolated", ToolPolicy(requires_confirmation=True))])
    monkeypatch.setattr(application_module, "open_tool_registry", lambda settings: nullcontext(registry))
    model = FakeChatModel([AIMessage(content="", tool_calls=[{"name": "protected_action", "args": {"value": "synthetic"},
        "id": "protected-call", "type": "tool_call"}]), AIMessage(content="已完成")])
    monkeypatch.setattr(graph_module, "create_chat_model", lambda settings: model)
    settings = isolated_settings(tmp_path)
    with TestClient(create_api(settings)) as client:
        assert client.get("/api/v1/health").status_code == 200
        session_id = client.post("/api/v1/sessions").json()["id"]
        response = client.post(f"/api/v1/sessions/{session_id}/turns", json={"message": "执行验证操作"})
        assert "review.required" in response.text and "turn.completed" not in response.text
        assert calls == []
    with TestClient(create_api(settings)) as client:
        pending = client.get(f"/api/v1/sessions/{session_id}/turn").json()
        assert pending["status"] == "awaiting_review" and pending["review"]["kind"] == "tool"
        turn_id = pending["id"]
        assert client.post(f"/api/v1/turns/{turn_id}/decision",
            json={"decision": "approve", "selected_item_ids": ["memory-0"]}).status_code == 422
        response = client.post(f"/api/v1/turns/{turn_id}/decision", json={"decision": "approve"})
        assert response.status_code == 200 and "message.completed" in response.text and "turn.completed" in response.text
        assert "memory.saved" not in response.text and calls == ["synthetic"]
        assert len(client.get(f"/api/v1/sessions/{session_id}").json()["messages"]) == 2
        item = client.post("/api/v1/memories", json={"text": "isolated-deletable-content"}).json()
        assert client.delete(f"/api/v1/memories/{item['id']}").status_code == 204
        assert client.get("/api/v1/memories").json()["items"] == []
        assert "isolated-deletable-content" not in settings.memory_path.read_text(encoding="utf-8")


def test_legacy_memory_interrupt_is_not_auto_approved(monkeypatch, tmp_path):
    settings = isolated_settings(tmp_path)
    repository = JsonSessionRepository(settings.session_dir)
    session = repository.create()
    source = new_source("只是一段旧对话", user_id="default", session_id=session.id)
    assistant = new_source("旧回答", user_id="default", session_id=session.id, role="assistant")

    def old_review(state):
        interrupt({"type": "memory_review", "request": {"candidates": [{"text": "must-not-be-approved"}]}})
        raise AssertionError("Old node must not resume")

    with open_checkpointer(settings) as checkpoint:
        workflow = StateGraph(ChatState)
        workflow.add_node("review_memory", old_review)
        workflow.add_edge(START, "review_memory")
        old = workflow.compile(checkpointer=checkpoint.saver)
        old.invoke({"question": source.content, "messages": [source.to_message(), assistant.to_message()]},
                   config={"configurable": {"thread_id": session.thread_id}})
    monkeypatch.setattr(graph_module, "create_chat_model", lambda settings: FakeChatModel([]))
    with open_human_chat_application(settings) as application:
        assert application.get_pending_review(session.id) is None
        assert application.list_memories() == []
        assert len(application.get_session_with_messages(session.id)[1]) == 2
