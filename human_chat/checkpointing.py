def create_checkpointer():
    try:
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
