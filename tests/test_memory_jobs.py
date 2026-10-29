from human_chat.memory.formation import FormationPlan
from human_chat.memory.jobs import MemoryJobs, MemoryWorker
from human_chat.memory.repository import SqliteMemoryRepository
from human_chat.memory.search import SqliteMemorySearch
from human_chat.memory.service import LongTermMemoryService
from tests.test_memory_maintenance import operation


def test_crash_after_record_commit_reuses_plan_and_idempotent_operation(tmp_path):
    jobs_path = tmp_path / "jobs.sqlite"
    repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    search = SqliteMemorySearch(None)
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
