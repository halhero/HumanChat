from human_chat.memory.formation import FormationPlan
from human_chat.memory.jobs import MemoryJobs, MemoryWorker
from human_chat.memory.repository import JsonMemoryRepository
from human_chat.memory.service import LongTermMemoryService
from tests.test_memory_maintenance import operation


def test_crash_after_record_commit_reuses_plan_and_receipt(tmp_path):
    path = tmp_path / "jobs.sqlite"
    service = LongTermMemoryService(JsonMemoryRepository(tmp_path / "m.json"), ("users", "default", "memory"))
    op = operation("seed")
    jobs = MemoryJobs(path)
    job_id = jobs.enqueue(user_id="default", thread_id="s1", checkpoint_id="cp1", source_id="source-seed", source_time="2026-01-01")
    jobs.save_plan(job_id, FormationPlan(operations=[op]))
    service.apply(op)  # Simulated process loss after canonical write, before job acknowledgement.
    jobs.close()
    jobs = MemoryJobs(path)

    def must_not_extract(job):
        raise AssertionError("A durable plan must not be extracted again")

    worker = MemoryWorker(jobs, None, service, must_not_extract)
    paused = MemoryWorker(jobs, None, service, must_not_extract, enabled=False)
    assert not paused.process_one() and not jobs.is_done(job_id)
    assert worker.process_one() and jobs.is_done(job_id)
    assert len(service.load().items) == 1 and service.load().items[0].version == 1
    assert jobs.next() is None
    jobs.close()


def test_delete_clears_pending_plan_payload(tmp_path):
    service = LongTermMemoryService(JsonMemoryRepository(tmp_path / "m.json"), ("users", "default", "memory"))
    service.apply(operation("seed"))
    item = service.load().items[0]
    jobs = MemoryJobs(tmp_path / "jobs.sqlite")
    service.on_delete(jobs.forget)
    job_id = jobs.enqueue(user_id="default", thread_id="s1", checkpoint_id="cp1", source_id="source-seed", source_time="2026-01-01")
    jobs.save_plan(job_id, FormationPlan(operations=[operation("seed")]))
    service.delete_by_id(item.id)
    assert jobs.is_done(job_id)
    assert jobs._db.execute("SELECT plan FROM memory_jobs WHERE id=?", (job_id,)).fetchone()[0] is None
    jobs.close()
