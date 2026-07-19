from human_chat.memory_models import LongTermMemory, create_default_memory


def test_long_term_memory_migrates_legacy_categories():
    memory = LongTermMemory(
        preferences=["偏好"],
        facts=["事实"],
        notes=["备注"],
    )

    assert [item.text for item in memory.items] == ["偏好", "事实", "备注"]
    assert all(item.source == "legacy" for item in memory.items)


def test_default_memory_returns_independent_instances():
    first = create_default_memory()
    second = create_default_memory()

    first.items.pop()

    assert len(first.items) + 1 == len(second.items)
