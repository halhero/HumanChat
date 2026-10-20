from contextlib import nullcontext

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

import human_chat.application as application_module
import human_chat.graph as graph_module
from human_chat.api.app import create_api
from human_chat.config import Settings
from human_chat.tool_provider import RegisteredTool, ToolPolicy, ToolRegistry
from tests.test_graph import FakeChatModel


def isolated_settings(tmp_path):
    return Settings(openai_api_key="isolated-placeholder", memory_semantic_enabled=False,
        memory_extraction_enabled=False, session_dir=tmp_path / "sessions", memory_path=tmp_path / "memory.json",
        checkpoint_path=tmp_path / "checkpoint.sqlite")


def test_http_tool_review_restart_preserves_tool_only_contract(monkeypatch, tmp_path):
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


def test_manual_memory_routes_are_not_exposed(monkeypatch, tmp_path):
    settings = isolated_settings(tmp_path)
    monkeypatch.setattr(graph_module, "create_chat_model", lambda settings: FakeChatModel([]))
    with TestClient(create_api(settings)) as client:
        assert not any("/memories" in path for path in client.get("/openapi.json").json()["paths"])
        assert client.get("/api/v1/memories").status_code == 404
        # A mounted frontend catch-all yields 405 for non-GET methods; API-only mode yields 404.
        assert client.post("/api/v1/memories", json={"text": "must-not-be-written"}).status_code in (404, 405)
        assert client.delete("/api/v1/memories/unknown").status_code in (404, 405)
        service = client.app.state.human_chat_application._memory.service
        assert service.inventory() == []
