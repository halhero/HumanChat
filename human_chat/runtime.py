from human_chat.config import Settings
from human_chat.checkpointing import create_checkpointer
from human_chat.graph import build_graph
from human_chat.session_models import SessionRecord, now_local
from human_chat.storage import create_session_repository


class ChatRuntime:
    def __init__(
        self,
        settings: Settings,
        session: SessionRecord | None = None,
        persist_session: bool = True,
        session_repository=None,
        checkpointer=None,
    ):
        self.settings = settings
        self.session = session or SessionRecord.create()
        self.persist_session = persist_session
        self.session_repository = session_repository or create_session_repository(settings)
        self.thread_id = self.session.thread_id
        self.checkpointer = checkpointer or create_checkpointer(settings)
        self.app = build_graph(settings, checkpointer=self.checkpointer)
        self.graph_config = {"configurable": {"thread_id": self.thread_id}}
        self.messages = []

    def ask(self, question: str) -> dict:
        graph_input = {"question": question}
        result = self.app.invoke(graph_input, config=self.graph_config)
        self._save_runtime_state(result)

        return result

    def resume(self, value: dict) -> dict:
        from langgraph.types import Command

        result = self.app.invoke(Command(resume=value), config=self.graph_config)
        self._save_runtime_state(result)
        return result

    def _save_runtime_state(self, result: dict) -> None:
        self.messages = result.get("messages", self.messages)

        if self.persist_session:
            self.session = self.session.model_copy(
                update={
                    "message_count": len(self.messages),
                    "updated_at": now_local(),
                }
            )
            self.session_repository.save(self.session)
