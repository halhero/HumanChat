from human_chat.memory_models import LongTermMemory, MemoryItem
from human_chat.memory_repository import JsonMemoryRepository, memory_path_for_namespace


def test_json_repository_creates_and_loads_default_memory(tmp_path):
    namespace = ("users", "default", "memory")
    path = tmp_path / "memory.json"
    repository = JsonMemoryRepository(path, namespace)

    memory = repository.load_memory(namespace)

    assert path.exists()
    assert memory.items


def test_json_repository_round_trip(tmp_path):
    namespace = ("users", "test-user", "memory")
    base_path = tmp_path / "memory.json"
    repository = JsonMemoryRepository(base_path, namespace)
    memory = LongTermMemory(items=[MemoryItem(text="测试记忆")])

    repository.save_memory(namespace, memory)

    assert [item.text for item in repository.load_memory(namespace).items] == ["测试记忆"]
    assert repository.path == tmp_path / "memory.test-user.json"


def test_memory_path_sanitizes_user_namespace(tmp_path):
    path = memory_path_for_namespace(
        tmp_path / "memory.json",
        ("users", "../unsafe user", "memory"),
    )

    assert path.parent == tmp_path
    assert path.name == "memory.unsafe_user.json"
