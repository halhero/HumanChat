from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

import human_chat.graph as graph_module
from human_chat.config import Settings
from human_chat.tool_provider import RegisteredTool, ToolRegistry


class FakeChatModel:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def invoke(self, messages):
        self.calls.append(list(messages))
        return self._responses.pop(0)


class FakeMemoryService:
    def format_for_prompt(self) -> str:
        return "长期记忆：\n- 用户希望理解修改原因"

    def add(self, text: str, source: str = "manual", confidence=None) -> bool:
        return True


@tool("lookup_context")
def lookup_context(value: str) -> str:
    """Return test project context."""
    return f"context:{value}"


def _build_test_graph(monkeypatch, model, registry=None):
    monkeypatch.setattr(graph_module, "create_chat_model", lambda settings: model)
    return graph_module.build_graph(
        Settings(memory_extraction_enabled=False),
        memory_service=FakeMemoryService(),
        tool_registry=registry or ToolRegistry([]),
    )


def test_direct_reply_reuses_first_model_response(monkeypatch):
    model = FakeChatModel([AIMessage(content="直接回答")])
    app = _build_test_graph(monkeypatch, model)

    result = app.invoke({"question": "你好"})

    assert result["assistant_text"] == "直接回答"
    assert len(model.calls) == 1
    assert isinstance(model.calls[0][0], SystemMessage)
    assert isinstance(model.calls[0][-1], HumanMessage)
    assert "用户希望理解修改原因" in model.calls[0][0].content


def test_tool_result_stays_in_same_model_conversation(monkeypatch):
    tool_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "lookup_context",
                "args": {"value": "graph"},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )
    model = FakeChatModel([tool_call, AIMessage(content="基于工具结果回答")])
    registry = ToolRegistry(
        [RegisteredTool(tool=lookup_context, source="test")]
    )
    app = _build_test_graph(monkeypatch, model, registry)

    result = app.invoke({"question": "查看项目"})

    assert result["assistant_text"] == "基于工具结果回答"
    assert len(model.calls) == 2
    assert any(isinstance(message, ToolMessage) for message in model.calls[1])
    assert result["tool_call_count"] == 1
    assert result["tool_events"][0]["status"] == "success"
