from human_chat.memory.models import MemoryItem


def test_memory_item_keeps_independent_history_and_evidence_lists():
    first = MemoryItem(text="第一条", last_operation_id="operation-1")
    second = MemoryItem(text="第二条")

    first.context_message_ids.append("message-1")

    assert first.last_operation_id == "operation-1"
    assert second.context_message_ids == []
