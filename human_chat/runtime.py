from human_chat.config import Settings
from human_chat.checkpointing import create_checkpointer
from human_chat.graph import build_graph
from human_chat.session_store import dicts_to_messages
from human_chat.storage import create_session_store


class ChatRuntime:
    def __init__(
        self,
        settings: Settings,
        session: dict | None = None,
        persist_session: bool = True,
        session_store=None,
        checkpointer=None,
    ):
        self.settings = settings
        self.session = session or {"messages": []}
        self.persist_session = persist_session
        self.session_store = session_store or create_session_store(settings)
        self.thread_id = self.session.get("id", "run_once")
        self.checkpointer = checkpointer or create_checkpointer(settings)
        self.app = build_graph(settings, checkpointer=self.checkpointer)
        self.graph_config = {"configurable": {"thread_id": self.thread_id}}
        self.messages = dicts_to_messages(self.session.get("messages", []))
        self._seed_checkpoint = bool(self.messages)

    def ask(self, question: str) -> dict:
        graph_input = {"question": question}
        if self._seed_checkpoint:
            graph_input["messages"] = self.messages
            self._seed_checkpoint = False

        result = self.app.invoke(graph_input, config=self.graph_config)
        self.messages = result.get("messages", self.messages)

        if self.persist_session and "id" in self.session:
            self.session["message_count"] = len(self.messages)
            self.session_store.save(self.session)

        return result
