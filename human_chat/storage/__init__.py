from human_chat.storage.json_store import JsonMemoryStore, JsonSessionStore


def create_session_store(settings):
    return JsonSessionStore(settings)


def create_memory_store(settings):
    return JsonMemoryStore(settings)


__all__ = [
    "JsonMemoryStore",
    "JsonSessionStore",
    "create_memory_store",
    "create_session_store",
]
