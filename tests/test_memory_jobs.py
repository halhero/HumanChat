from human_chat.memory.formation import FormationPlan
from human_chat.memory.index_store import SqliteMemoryIndexStore
from human_chat.memory.jobs import MemoryJobs, MemoryWorker
from human_chat.memory.repository import SqliteMemoryRepository
from human_chat.memory.service import LongTermMemoryService
from tests.test_memory_maintenance import operation


def test_crash_after_record_commit_reuses_plan_and_idempotent_operation(tmp_path):
    jobs_path = tmp_path / "jobs.sqlite"
    repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    search = SqliteMemoryIndexStore(None)
    service = LongTermMemoryService(repository, ("users", "default", "memory"), search)
    op = operation("seed")
    jobs = MemoryJobs(jobs_path)
    job_id = jobs.enqueue(
        user_id="default",
        thread_id="s1",
        checkpoint_id="cp1",
        source_id="source-seed",
        source_time="2026-01-01",
    )
    jobs.save_plan(job_id, FormationPlan(operations=[op]))
    committed = service.apply(op)
    jobs.close()
    jobs = MemoryJobs(jobs_path)

    def must_not_extract(job):
        raise AssertionError("A durable plan must not be extracted again")

    worker = MemoryWorker(jobs, None, service, must_not_extract)
    paused = MemoryWorker(jobs, None, service, must_not_extract, enabled=False)
    assert not paused.process_one() and not jobs.is_done(job_id)
    assert worker.process_one() and jobs.is_done(job_id)
    assert service.get_item(committed.item_id).version == 1
    assert jobs.next() is None
    jobs.close()
    search.close()
    repository.close()


def test_pending_source_is_durable_and_promoted_atomically(tmp_path):
    path = tmp_path / "jobs.sqlite"
    jobs = MemoryJobs(path)
    jobs.register_pending(
        user_id="default",
        thread_id="thread-1",
        source_id="source-1",
        source_time="2026-01-01",
    )
    jobs.close()

    jobs = MemoryJobs(path)
    assert [source["source_id"] for source in jobs.pending_sources()] == ["source-1"]

    job_id = jobs.promote_pending("source-1", "checkpoint-1")

    assert jobs.pending_sources() == []
    assert jobs.next()["id"] == job_id
    assert jobs.promote_pending("source-1", "checkpoint-2") is None
    assert jobs.next()["checkpoint_id"] == "checkpoint-1"
    jobs.close()
