from human_chat.config import Settings
from human_chat.memory_repository import default_memory_namespace
from human_chat.memory_resources import open_memory_resource


def test_json_memory_resource_is_persistent(tmp_path):
    settings = Settings(memory_path=tmp_path / "memory.json", memory_backend="json")

    with open_memory_resource(settings) as resource:
        assert resource.backend == "json"
        assert resource.persistent
        assert resource.store is None


def test_memory_store_resource_uses_langgraph_store(tmp_path):
    settings = Settings(memory_path=tmp_path / "memory.json", memory_backend="memory")

    with open_memory_resource(settings) as resource:
        assert resource.backend == "memory"
        assert not resource.persistent
        assert resource.store is not None
        assert resource.service.add("运行时记忆")
        namespace = default_memory_namespace(settings)
        assert resource.store.search(namespace)[0].value["text"] == "运行时记忆"
