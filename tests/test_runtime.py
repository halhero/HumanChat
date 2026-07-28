from langchain_core.messages import AIMessage, HumanMessage

from human_chat.config import Settings
from human_chat.runtime import ChatRuntime
from human_chat.session_models import SessionRecord


class FakeGraph:
    def __init__(self):
        self.contexts = []

    def invoke(self, graph_input, config, context):
        self.contexts.append(context)
        question = graph_input["question"]
        return {
            "messages": [
                HumanMessage(content=question),
                AIMessage(content="answer"),
            ]
        }


class RecordingSessionRepository:
    def __init__(self):
        self.saved = []

    def save(self, session):
        self.saved.append(session)


def test_runtime_persists_typed_session_metadata():
    repository = RecordingSessionRepository()
    session = SessionRecord.create()
    graph = FakeGraph()
    runtime = ChatRuntime(
        settings=Settings(),
        session=session,
        app=graph,
        session_repository=repository,
        checkpoint_backend="sqlite",
        checkpoint_persistent=True,
    )

    runtime.ask("hello")

    assert runtime.session.message_count == 2
    assert runtime.session.recoverable
    assert runtime.session.checkpoint_backend == "sqlite"
    assert repository.saved[-1] == runtime.session
    assert graph.contexts[-1].user_id == runtime.settings.memory_user_id


def test_ephemeral_runtime_does_not_require_session_repository():
    runtime = ChatRuntime(
        settings=Settings(),
        session=SessionRecord.create(),
        app=FakeGraph(),
        persist_session=False,
    )

    assert runtime.ask("hello")["messages"]
