import asyncio
import time
from contextlib import nullcontext
from threading import Event

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

import human_chat.graph as graph_module
from human_chat.config import Settings
from human_chat.tool_provider import RegisteredTool, ToolRegistry
from human_chat.tool_provider import ToolPolicy
from human_chat.application import open_human_chat_application
from human_chat.conversation import ConversationService, SessionBusyError
from human_chat.memory.context import Applicability, ContextAssessment, MemoryContextBuilder
from human_chat.memory.formation import FormationPlan, MemoryFormation
from human_chat.memory.models import Assertion, Evidence, Mutation
from human_chat.memory.repository import LangGraphMemoryRepository
from human_chat.memory.retrieval import MemoryRetriever
from human_chat.memory.service import LongTermMemoryService
from langgraph.store.memory import InMemoryStore
from tests.test_memory_context import AssessmentModel
from tests.test_memory_maintenance import remember
import human_chat.application as application_module


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


@tool("lookup_context")
def lookup_context(value: str) -> str:
    """Return test project context."""
    return f"context:{value}"


def _build_test_graph(monkeypatch, model, registry=None):
    monkeypatch.setattr(graph_module, "create_chat_model", lambda settings: model)
    service = LongTermMemoryService(LangGraphMemoryRepository(InMemoryStore()), ("users", "default", "memory"))
    item = remember(service, "用户希望理解修改原因")
    selector = AssessmentModel(ContextAssessment(task="讲解", memories=[Applicability(id=f"{item.id}@1",
        purpose="guidance", decision="needed", reason="适用")]))
    return graph_module.build_graph(
        Settings(memory_extraction_enabled=False),
        memory_service=service,
        context_builder=MemoryContextBuilder(MemoryRetriever(service), selector, Settings()),
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
    assert model.calls[0][-1].id == result["messages"][0].id
    assert result["messages"][0].additional_kwargs["human_chat_source"]["occurred_at"]


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


def test_memory_background_does_not_hold_turn_and_survives_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(
        graph_module, "create_chat_model",
        lambda settings: FakeChatModel([AIMessage(content="reply")]),
    )
    entered, release = Event(), Event()

    def prepare(self, batch, job_id):
        entered.set()
        assert release.wait(5)
        source = batch.new_messages[0]
        return FormationPlan(operations=[Mutation(id=job_id, user_id="default", kind="add", text="用户喜欢Python",
            assertion=Assertion(subject="user:default", relation="喜欢", value="Python"),
            evidence=[Evidence(message_id=source.id, user_id="default", session_id=source.session_id,
                               message_time=source.occurred_at, quote=source.content)])])

    monkeypatch.setattr(MemoryFormation, "prepare", prepare)
    settings = Settings(
        openai_api_key="validation-placeholder",
        session_dir=tmp_path / "sessions",
        memory_path=tmp_path / "memory.json",
        checkpoint_path=tmp_path / "checkpoint.sqlite",
        memory_semantic_enabled=False,
    )

    async def run():
        with open_human_chat_application(settings) as application:
            first = ConversationService(application)
            session = application.create_session()
            stream = await first.start_turn(session.id, "我喜欢Python")
            events = [event async for event in first.iter_events(stream)]
            try:
                assert events[-1].type == "turn.completed"
                assert not any("memory" in event.type or event.type == "review.required" for event in events)
                assert await asyncio.to_thread(entered.wait, 2)
                assert application._memory.service.load().items == []
                assert await first.get_session_turn(session.id) is None
            finally:
                release.set()
            deadline = time.monotonic() + 3
            while not application._memory.service.load().items and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert len(application._memory.service.load().items) == 1
            emitted_id = next(
                event.data["message"]["id"]
                for event in events if event.type == "message.completed"
            )
            assert application.get_session_with_messages(session.id)[1][-1].id == emitted_id
            await first.shutdown()

        with open_human_chat_application(settings) as application:
            recovered = ConversationService(application)
            assert len(application._memory.service.load().items) == 1
            assert application._memory_jobs.next() is None
            assert await recovered.get_session_turn(session.id) is None
            await recovered.shutdown()

    asyncio.run(run())


def test_cancelled_tool_review_does_not_return_from_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(
        graph_module, "create_chat_model",
        lambda settings: FakeChatModel([AIMessage(content="", tool_calls=[
            {"name": "lookup_context", "args": {"value": "test"}, "id": "protected-1", "type": "tool_call"}])]),
    )
    registry = ToolRegistry([RegisteredTool(tool=lookup_context, source="test", policy=ToolPolicy(requires_confirmation=True))])
    monkeypatch.setattr(application_module, "open_tool_registry", lambda settings: nullcontext(registry))
    settings = Settings(
        openai_api_key="validation-placeholder",
        session_dir=tmp_path / "sessions",
        memory_path=tmp_path / "memory.json",
        checkpoint_path=tmp_path / "checkpoint.sqlite",
        memory_extraction_enabled=False,
        memory_semantic_enabled=False,
    )

    async def run():
        with open_human_chat_application(settings) as application:
            service = ConversationService(application)
            session = application.create_session()
            stream = await service.start_turn(session.id, "question")
            events = [event async for event in service.iter_events(stream)]
            assert events[-1].type == "review.required" and events[-1].data["kind"] == "tool"
            assert await service.cancel_turn(stream.turn_id) == "cancelled"
            assert application.get_pending_review(session.id) is None
            assert application._memory.service.load().items == []
            assert application.get_session_with_messages(session.id)[1] == []
            fresh_service = ConversationService(application)
            assert await fresh_service.get_session_turn(session.id) is None
            await service.shutdown()

    asyncio.run(run())


def test_failed_model_turn_releases_session_for_retry(monkeypatch, tmp_path):
    class FailOnceModel(FakeChatModel):
        def invoke(self, messages):
            if not self.calls:
                self.calls.append(list(messages))
                raise RuntimeError("isolated provider failure")
            return super().invoke(messages)

    model = FailOnceModel([AIMessage(content="recovered reply")])
    monkeypatch.setattr(graph_module, "create_chat_model", lambda settings: model)
    settings = Settings(
        openai_api_key="validation-placeholder",
        memory_extraction_enabled=False,
        memory_semantic_enabled=False,
        session_dir=tmp_path / "sessions",
        memory_path=tmp_path / "memory.json",
        checkpoint_path=tmp_path / "checkpoint.sqlite",
    )

    async def run():
        with open_human_chat_application(settings) as application:
            service = ConversationService(application)
            session = application.create_session()
            stream = await service.start_turn(session.id, "failing question")
            events = [event async for event in service.iter_events(stream)]
            assert events[-1].type == "turn.failed"
            assert "isolated provider failure" not in events[-1].data["message"]
            assert await service.get_session_turn(session.id) is None

            retry = await service.start_turn(session.id, "retry question")
            events = [event async for event in service.iter_events(retry)]
            assert events[-1].type == "turn.completed"
            assert application.get_session_with_messages(session.id)[1][-1].content == "recovered reply"
            await service.shutdown()

    asyncio.run(run())
