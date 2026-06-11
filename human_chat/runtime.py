from human_chat.config import Settings
from human_chat.graph import build_graph
from human_chat.session_store import dicts_to_messages, messages_to_dicts, save_session


class ChatRuntime:
    def __init__(self, settings: Settings, session: dict | None = None, persist_session: bool = True):
        self.settings = settings
        self.session = session or {"messages": []}
        self.persist_session = persist_session
        self.app = build_graph(settings)
        self.messages = dicts_to_messages(self.session.get("messages", []))

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
            save_session(self.settings, self.session)

        return result
