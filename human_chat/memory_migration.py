from dataclasses import dataclass

from human_chat.config import Settings, load_settings
from human_chat.memory_repository import (
    JsonMemoryRepository,
    MemoryRepository,
    default_memory_namespace,
)
from human_chat.memory_resources import open_memory_resource


@dataclass(frozen=True)
class MemoryMigrationReport:
    copied: int = 0
    updated: int = 0
    skipped: int = 0


def migrate_json_memory(
    settings: Settings,
    target: MemoryRepository,
) -> MemoryMigrationReport:
    namespace = default_memory_namespace(settings)
    source = JsonMemoryRepository(settings.memory_path)
    copied = 0
    updated = 0
    skipped = 0

    for item in source.list_items(namespace):
        existing = target.get_item(namespace, item.id)
        if existing == item:
            skipped += 1
            continue
        target.upsert_item(namespace, item)
        if existing is None:
            copied += 1
        else:
            updated += 1

    return MemoryMigrationReport(
        copied=copied,
        updated=updated,
        skipped=skipped,
    )


def main() -> None:
    settings = load_settings()
    if settings.memory_backend.strip().lower() != "postgres":
        raise RuntimeError(
            "迁移目标必须显式配置 HUMANCHAT_MEMORY_BACKEND=postgres。"
        )

    with open_memory_resource(settings) as target:
        report = migrate_json_memory(settings, target.repository)

    print(
        "长期记忆迁移完成："
        f"copied={report.copied} "
        f"updated={report.updated} "
        f"skipped={report.skipped}"
    )


if __name__ == "__main__":
    main()
