from human_chat.memory_store import LongTermMemory, add_memory_item, delete_memory_item, format_memory_for_prompt


def test_add_memory_item_deduplicates():
    memory = LongTermMemory()

    assert add_memory_item(memory, "用户喜欢中文讲解。")
    assert not add_memory_item(memory, "用户喜欢中文讲解。")
    assert memory.items[0].text == "用户喜欢中文讲解。"


def test_delete_memory_item_uses_one_based_index():
    memory = LongTermMemory(facts=["事实一", "事实二"])

    assert delete_memory_item(memory, 2) == "事实二"
    assert [item.text for item in memory.items] == ["事实一"]
    assert delete_memory_item(memory, 3) is None


def test_format_memory_for_prompt_groups_sections():
    memory = LongTermMemory(
        preferences=["偏好"],
        facts=["事实"],
        notes=["备注"],
    )

    prompt = format_memory_for_prompt(memory)

    assert "长期记忆：" in prompt
    assert "- 偏好" in prompt
    assert "- 事实" in prompt
    assert "- 备注" in prompt
