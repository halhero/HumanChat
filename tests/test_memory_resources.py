from human_chat.config import Settings
from human_chat.memory_resources import open_memory_resource


def test_json_memory_resource_is_persistent(tmp_path):
    settings = Settings(memory_path=tmp_path / "memory.json", memory_backend="json")

    with open_memory_resource(settings) as resource:
        assert resource.backend == "json"
        assert resource.persistent
        assert resource.service.load().items == []


def test_memory_store_resource_uses_langgraph_store(tmp_path):
    settings = Settings(memory_path=tmp_path / "memory.json", memory_backend="memory")

    with open_memory_resource(settings) as resource:
        assert resource.backend == "memory"
        assert not resource.persistent
        assert resource.service.add("运行时记忆")
        assert [item.text for item in resource.service.load().items] == ["运行时记忆"]
