from human_chat.config import Settings
from human_chat.checkpointing import create_checkpointer
from human_chat.graph import build_graph
from human_chat.llm import create_chat_model
from human_chat.logging_config import get_logger
from human_chat.memory_extractor import extract_memory_candidates
from human_chat.session_store import dicts_to_messages, messages_to_dicts
from human_chat.storage import create_session_store


logger = get_logger(__name__)


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
        self.checkpointer = checkpointer or create_checkpointer()
        self.app = build_graph(settings, checkpointer=self.checkpointer)
        self.graph_config = {"configurable": {"thread_id": self.thread_id}}
        self.messages = dicts_to_messages(self.session.get("messages", []))
        self._seed_checkpoint = bool(self.messages)
        self.memory_llm = create_chat_model(settings) if settings.memory_extraction_enabled else None

    def ask(self, question: str) -> dict:
        graph_input = {"question": question}
        if self._seed_checkpoint:
            graph_input["messages"] = self.messages
            self._seed_checkpoint = False

        result = self.app.invoke(graph_input, config=self.graph_config)
        self.messages = result.get("messages", self.messages)

        if self.persist_session and "id" in self.session:
            self.session["messages"] = messages_to_dicts(self.messages)
            self.session_store.save(self.session)

        result["memory_candidates"] = self._extract_memory_candidates(question, result)
        return result

    def _extract_memory_candidates(self, question: str, result: dict) -> list[dict]:
        if self.memory_llm is None:
            return []

        assistant_text = result.get("assistant_text", "")
        if not assistant_text:
            return []

        try:
            candidates = extract_memory_candidates(self.memory_llm, question, assistant_text)
        except Exception:
            logger.exception("Failed to extract memory candidates")
            return []

        return [candidate.dict() for candidate in candidates]
