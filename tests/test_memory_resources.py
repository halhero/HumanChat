from human_chat.config import Settings
from human_chat.memory.resources import open_memory_resource
from tests.test_memory_maintenance import remember


def test_json_memory_resource_is_persistent(tmp_path):
    settings = Settings(memory_path=tmp_path / "memory.json", memory_backend="json", memory_semantic_enabled=False)

    with open_memory_resource(settings) as resource:
        assert resource.backend == "json"
        assert resource.persistent
        assert resource.service.load().items == []


def test_memory_store_resource_uses_langgraph_store(tmp_path):
    settings = Settings(memory_path=tmp_path / "memory.json", memory_backend="memory", memory_semantic_enabled=False)

    with open_memory_resource(settings) as resource:
        assert resource.backend == "memory"
        assert not resource.persistent
        item = remember(resource.service, "运行时记忆")
        assert resource.service.load().items == [item]
