from human_chat.config import Settings
from human_chat.graph import build_graph
from human_chat.llm import create_chat_model
from human_chat.logging_config import get_logger
from human_chat.memory_extractor import extract_memory_candidates
from human_chat.session_store import dicts_to_messages, messages_to_dicts
from human_chat.storage import JsonSessionStore


logger = get_logger(__name__)


class ChatRuntime:
    def __init__(
        self,
        settings: Settings,
        session: dict | None = None,
        persist_session: bool = True,
        session_store=None,
    ):
        self.settings = settings
        self.session = session or {"messages": []}
        self.persist_session = persist_session
        self.session_store = session_store or JsonSessionStore(settings)
        self.app = build_graph(settings)
        self.messages = dicts_to_messages(self.session.get("messages", []))
        self.memory_llm = create_chat_model(settings) if settings.memory_extraction_enabled else None

    def ask(self, question: str) -> dict:
        result = self.app.invoke(
            {
                "question": question,
                "messages": self.messages,
            }
        )
        self.messages = result.get("messages", self.messages)

        if self.persist_session and "id" in self.session:
            self.session["messages"] = messages_to_dicts(self.messages)
            self.session_store.save(self.session)

        result["memory_candidates"] = self._extract_memory_candidates(question, result)
        return result

    def _extract_memory_candidates(self, question: str, result: dict) -> list[dict]:
        if self.memory_llm is None:
            return []

        assistant_text = result.get("assistant_text") or result.get("tts_text", "")
        if not assistant_text:
            return []

        try:
            candidates = extract_memory_candidates(self.memory_llm, question, assistant_text)
        except Exception:
            logger.exception("Failed to extract memory candidates")
            return []

        return [candidate.dict() for candidate in candidates]
