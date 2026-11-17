import asyncio
import json
import time
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.store.memory import InMemoryStore

import human_chat.application as application_module
import human_chat.graph as graph_module
from human_chat.application import HumanChatApplication, open_human_chat_application
from human_chat.config import Settings
from human_chat.conversation import ConversationService
from human_chat.memory.context import (
    Applicability,
    ContextAssessment,
    MemoryContext,
    MemoryContextBuilder,
)
from human_chat.memory.formation import FormationPlan, MemoryFormation
from human_chat.memory.index_store import MemoryIndexEntry, SqliteMemoryIndexStore
from human_chat.memory.jobs import MemoryJobs
from human_chat.memory.models import Assertion, Evidence, Mutation
from human_chat.memory.repository import LangGraphMemoryRepository
from human_chat.memory.retrieval import MemoryRetriever, RecallResult, RecalledMemory
from human_chat.memory.service import LongTermMemoryService
from human_chat.messages import new_source
from human_chat.request_understanding import TriageDecision
from human_chat.tool_provider import RegisteredTool, ToolPolicy, ToolRegistry
from tests.test_memory_context import AssessmentModel
from tests.test_memory_maintenance import operation


class FakeChatModel:
    def __init__(self, responses, understanding_responses=None):
        self._responses = list(responses)
        self._understanding_responses = list(understanding_responses or [])
        self.calls = []
        self.understanding_calls = []
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def invoke(self, messages):
        self.calls.append(list(messages))
        return self._responses.pop(0)

    def with_structured_output(self, schema, *, method):
        parent = self

        class StructuredModel:
            def invoke(self, messages):
                parent.understanding_calls.append(list(messages))
                if parent._understanding_responses:
                    result = parent._understanding_responses.pop(0)
                    if isinstance(result, Exception):
                        raise result
                    return result
                payload = json.loads(messages[-1].content)
                if schema is TriageDecision:
                    return {
                        "outcome": "resolved",
                        "resolved_request": payload["current_user_input"],
                    }
                raise AssertionError("unexpected structured model call")

        return StructuredModel()


@tool("lookup_context")
def lookup_context(value: str) -> str:
    """Return test project context."""
    return f"context:{value}"


def _build_test_graph(
    monkeypatch,
    model,
    registry=None,
    context_builder=None,
    memory_retriever=None,
):
    monkeypatch.setattr(graph_module, "create_chat_model", lambda settings: model)
    search = SqliteMemoryIndexStore(None)
    service = LongTermMemoryService(
        LangGraphMemoryRepository(InMemoryStore()),
        ("users", "default", "memory"),
        search,
    )
    result = service.apply(operation(
        "global-explanation-guidance",
        assertion=Assertion(
            subject="user:default",
            relation="回答方式",
            value="用户希望理解修改原因",
            scope="全局",
        ),
    ))
    item = service.get_item(result.item_id)
    selector = AssessmentModel(ContextAssessment(task="讲解", memories=[Applicability(id=f"{item.id}@1",
        purpose="guidance", decision="needed", reason="适用")]))
    retriever = memory_retriever or MemoryRetriever(search)
    return graph_module.build_graph(
        Settings(memory_extraction_enabled=False),
        memory_service=service,
        memory_retriever=retriever,
        context_builder=context_builder or MemoryContextBuilder(
            retriever,
            selector,
            Settings(),
        ),
        tool_registry=registry or ToolRegistry([]),
    )


def test_direct_reply_reuses_first_model_response(monkeypatch):
    model = FakeChatModel([AIMessage(content="直接回答")])
    app = _build_test_graph(monkeypatch, model)

    result = app.invoke({"question": "你好"})

    assert result["assistant_text"] == "直接回答"
    assert result["tool_messages"] == []
    assert len(model.calls) == 1
    assert isinstance(model.calls[0][0], SystemMessage)
    assert isinstance(model.calls[0][-1], HumanMessage)
    assert "用户希望理解修改原因" in model.calls[0][0].content
    assert model.calls[0][-1].id == result["messages"][0].id
    assert result["messages"][0].additional_kwargs["human_chat_source"]["occurred_at"]


def test_resolved_request_drives_recall_and_agent_without_replacing_original(monkeypatch):
    class CapturingContextBuilder:
        def __init__(self):
            self.calls = []

        def build(self, question, recent_context):
            self.calls.append((question, recent_context))
            return MemoryContext()

    context_builder = CapturingContextBuilder()
    model = FakeChatModel(
        [AIMessage(content="继续处理")],
        understanding_responses=[{
            "outcome": "resolved",
            "resolved_request": "继续整理 HumanChat 的 checkpoint 恢复流程。",
        }],
    )
    app = _build_test_graph(
        monkeypatch,
        model,
        context_builder=context_builder,
    )

    result = app.invoke({"question": "继续"})

    assert context_builder.calls[0][0] == "继续整理 HumanChat 的 checkpoint 恢复流程。"
    assert "继续整理 HumanChat 的 checkpoint 恢复流程" in model.calls[0][0].content
    assert "原始用户消息为准" in model.calls[0][0].content
    assert model.calls[0][-1].content == "继续"
    assert result["input_source"]["content"] == "继续"
    assert result["messages"][0].content == "继续"


def test_memory_assisted_understanding_keeps_triage_data_local(monkeypatch):
    class CapturingContextBuilder:
        def __init__(self):
            self.calls = []

        def build(self, question, recent_context):
            self.calls.append((question, recent_context))
            return MemoryContext()

    entry = MemoryIndexEntry(
        id="project@2",
        parent_id="project",
        parent_version=2,
        version=2,
        text="HumanChat 当前正在完善请求理解流程。",
        status="current",
        assertion=None,
        observed_at=None,
    )

    class Retriever:
        def __init__(self):
            self.calls = []

        def recall(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return RecallResult([RecalledMemory(entry)], [])

    retriever = Retriever()
    context_builder = CapturingContextBuilder()
    model = FakeChatModel(
        [AIMessage(content="继续处理")],
        understanding_responses=[
            {
                "outcome": "search_memory",
                "memory_query": "HumanChat 项目当前阶段",
            },
            {
                "resolved_request": "继续完善 HumanChat 的请求理解流程。",
                "clarification_question": "",
            },
        ],
    )
    app = _build_test_graph(
        monkeypatch,
        model,
        context_builder=context_builder,
        memory_retriever=retriever,
    )

    result = app.invoke({"question": "继续上次的 HumanChat 项目"})

    assert retriever.calls == [(
        "HumanChat 项目当前阶段",
        {"purpose": "triage", "limit": 5},
    )]
    assert context_builder.calls[0][0] == "继续完善 HumanChat 的请求理解流程。"
    assert len(model.understanding_calls) == 2
    assert "memory_query" not in result
    assert "memory_candidates" not in result
    assert result["messages"][0].content == "继续上次的 HumanChat 项目"


def test_clarification_is_committed_without_memory_agent_or_tools(monkeypatch):
    class FailingContextBuilder:
        def build(self, question, recent_context):
            raise AssertionError("clarification path called memory context builder")

    model = FakeChatModel(
        [],
        understanding_responses=[{
            "outcome": "clarify",
            "clarification_question": "你希望把哪份内容发送给哪位接收者？",
        }],
    )
    app = _build_test_graph(
        monkeypatch,
        model,
        context_builder=FailingContextBuilder(),
    )

    result = app.invoke({"question": "把它发给他"})

    assert result["assistant_text"] == "你希望把哪份内容发送给哪位接收者？"
    assert result["tool_messages"] == []
    assert [message.content for message in result["messages"]] == [
        "把它发给他",
        "你希望把哪份内容发送给哪位接收者？",
    ]
    assert model.calls == []
    assert len(model.understanding_calls) == 1
    assert result["tool_call_count"] == 0


def test_request_understanding_failure_stops_before_context_and_agent(monkeypatch):
    class FailingContextBuilder:
        def build(self, question, recent_context):
            raise AssertionError("failed understanding called memory context builder")

    model = FakeChatModel(
        [],
        understanding_responses=[{
            "outcome": "resolved",
            "resolved_request": "",
        }],
    )
    app = _build_test_graph(
        monkeypatch,
        model,
        context_builder=FailingContextBuilder(),
    )

    with pytest.raises(ValueError, match="resolved_request must not be empty"):
        app.invoke({"question": "无法理解的请求"})

    assert model.calls == []


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
    assert isinstance(result["tool_messages"][-1], ToolMessage)
    assert not any(
        isinstance(message, AIMessage) and message.content == "基于工具结果回答"
        for message in result["tool_messages"]
    )
    assert result["tool_call_count"] == 1
    assert result["tool_events"][0]["status"] == "success"
    assert len(model.understanding_calls) == 1


def test_tool_limit_reply_uses_final_reply_state(monkeypatch):
    requests = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "lookup_context",
                "args": {"value": f"round-{index}"},
                "id": f"call-{index}",
                "type": "tool_call",
            }],
        )
        for index in range(4)
    ]
    model = FakeChatModel([*requests, AIMessage(content="工具调用达到上限后的回答")])
    registry = ToolRegistry(
        [RegisteredTool(tool=lookup_context, source="test")]
    )
    app = _build_test_graph(monkeypatch, model, registry)

    result = app.invoke({"question": "连续查询项目"})

    assert result["assistant_text"] == "工具调用达到上限后的回答"
    assert result["tool_call_count"] == graph_module.MAX_TOOL_CALL_ROUNDS
    assert isinstance(result["tool_messages"][-1], ToolMessage)
    assert result["tool_messages"][-1].content.startswith("[tool_limit]")
    assert not any(
        isinstance(message, AIMessage) and message.content == "工具调用达到上限后的回答"
        for message in result["tool_messages"]
    )


def test_completed_turn_enqueues_known_source_without_converting_history(monkeypatch):
    source = new_source("current", user_id="default", session_id="session")
    assistant = new_source("reply", user_id="default", session_id="session", role="assistant")
    enqueued = []
    application = HumanChatApplication.__new__(HumanChatApplication)
    application._settings = Settings(memory_extraction_enabled=True)
    application._memory_worker = SimpleNamespace(notify=lambda: None)
    monkeypatch.setattr(
        application_module,
        "history_sources",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("full history converted")),
    )
    snapshot = SimpleNamespace(
        values={
            "input_source": source.model_dump(mode="json"),
            "messages": [
                HumanMessage(id="old-user", content="old"),
                AIMessage(id="old-assistant", content="old reply"),
                source.to_message(),
                assistant.to_message(),
            ],
        },
        config={"configurable": {"checkpoint_id": "checkpoint"}},
    )
    application._memory_jobs = SimpleNamespace(
        promote_pending=lambda source_id, checkpoint_id: enqueued.append({
            "source_id": source_id,
            "checkpoint_id": checkpoint_id,
        }),
    )

    application._enqueue_memory_source(
        SimpleNamespace(id="session", thread_id="session"),
        snapshot,
    )

    assert enqueued[0]["source_id"] == source.id


def test_startup_does_not_scan_session_repository(monkeypatch, tmp_path):
    class NoScanRepository:
        def list_recent(self, limit=10):
            raise AssertionError("startup scanned all sessions")

    monkeypatch.setattr(
        graph_module,
        "create_chat_model",
        lambda settings: FakeChatModel([]),
    )
    settings = Settings(
        openai_api_key="validation-placeholder",
        memory_path=tmp_path / "memory.sqlite",
        memory_semantic_enabled=False,
    )

    with open_human_chat_application(
        settings,
        session_repository=NoScanRepository(),
        checkpoint_backend="memory",
    ):
        pass


def test_startup_recovers_only_registered_pending_source(tmp_path):
    source = new_source("current", user_id="default", session_id="thread-1")
    assistant = new_source("reply", user_id="default", session_id="thread-1", role="assistant")
    snapshot = SimpleNamespace(
        values={
            "input_source": source.model_dump(mode="json"),
            "messages": [source.to_message(), assistant.to_message()],
        },
        config={"configurable": {"checkpoint_id": "checkpoint-1"}},
        interrupts=[],
    )
    requested = []
    application = HumanChatApplication.__new__(HumanChatApplication)
    application._settings = Settings(memory_extraction_enabled=True)
    application._memory_jobs = MemoryJobs(tmp_path / "jobs.sqlite")
    application._memory_jobs.register_pending(
        user_id="default",
        thread_id="thread-1",
        source_id=source.id,
        source_time=source.occurred_at,
    )
    application._graph = SimpleNamespace(get_state=lambda config: (
        requested.append(config["configurable"]["thread_id"]),
        snapshot,
    )[1])

    application._recover_pending_memory_sources()

    assert requested == ["thread-1"]
    assert application._memory_jobs.pending_sources() == []
    assert application._memory_jobs.next()["source_id"] == source.id
    application._memory_jobs.close()


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
        memory_path=tmp_path / "memory.sqlite",
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
                assert not application._memory.retriever.has_documents()
                assert await first.get_session_turn(session.id) is None
            finally:
                release.set()
            deadline = time.monotonic() + 3
            while not application._memory.retriever.has_documents() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert application._memory.retriever.has_documents()
            emitted_id = next(
                event.data["message"]["id"]
                for event in events if event.type == "message.completed"
            )
            assert application.get_session_with_messages(session.id)[1][-1].id == emitted_id
            await first.shutdown()

        with open_human_chat_application(settings) as application:
            recovered = ConversationService(application)
            assert application._memory.retriever.has_documents()
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
        memory_path=tmp_path / "memory.sqlite",
        checkpoint_path=tmp_path / "checkpoint.sqlite",
        memory_extraction_enabled=True,
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
            assert application._memory_jobs.pending_sources() == []
            assert not application._memory.retriever.has_documents()
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
        memory_path=tmp_path / "memory.sqlite",
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
