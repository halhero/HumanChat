"""Single-process durable maintenance jobs, owned by the application lifecycle."""

import sqlite3
import time
from pathlib import Path
from threading import Event, RLock, Thread
from uuid import NAMESPACE_URL, uuid5

from human_chat.logging_config import get_logger
from human_chat.memory.formation import FormationPlan

logger = get_logger(__name__)


class MemoryJobs:
    def __init__(self, path: Path | None):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = RLock()
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.execute("""CREATE TABLE IF NOT EXISTS memory_jobs (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, thread_id TEXT NOT NULL,
            checkpoint_id TEXT NOT NULL, source_id TEXT NOT NULL, source_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', plan TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0, error TEXT, outcome TEXT)""")
        self._db.commit()

    def enqueue(self, *, user_id, thread_id, checkpoint_id, source_id, source_time):
        job_id = uuid5(NAMESPACE_URL, f"memory-job:{user_id}:{source_id}").hex
        with self._lock, self._db:
            self._db.execute("""INSERT OR IGNORE INTO memory_jobs
                (id,user_id,thread_id,checkpoint_id,source_id,source_time) VALUES (?,?,?,?,?,?)""",
                (job_id, user_id, thread_id, checkpoint_id, source_id, str(source_time)))
        return job_id

    def next(self):
        with self._lock:
            row = self._db.execute("""SELECT * FROM memory_jobs
                WHERE status IN ('pending','planned') AND retry_at <= ? ORDER BY source_time,id LIMIT 1""",
                (time.time(),)).fetchone()
            return dict(row) if row else None

    def save_plan(self, job_id, plan):
        with self._lock, self._db:
            self._db.execute("UPDATE memory_jobs SET plan=?,status='planned' WHERE id=? AND status!='done'",
                             (plan.model_dump_json(), job_id))

    def finish(self, job_id, outcome):
        with self._lock, self._db:
            self._db.execute("UPDATE memory_jobs SET status='done',plan=NULL,error=NULL,outcome=? WHERE id=?",
                             (outcome, job_id))

    def failed(self, job):
        attempts = job["attempts"] + 1
        with self._lock, self._db:
            self._db.execute("UPDATE memory_jobs SET attempts=?,status=?,retry_at=?,error=? WHERE id=? AND status!='done'",
                (attempts, "failed" if attempts >= 3 else "planned" if job["plan"] else "pending",
                 time.time() + 2 ** attempts, "maintenance_failed", job["id"]))

    def is_done(self, job_id):
        with self._lock:
            return self._db.execute("SELECT status FROM memory_jobs WHERE id=?", (job_id,)).fetchone()[0] == "done"

    def close(self):
        self._db.close()


class MemoryWorker:
    def __init__(self, jobs, formation, service, load_source, *, enabled=True):
        self.jobs, self.formation, self.service = jobs, formation, service
        self.load_source = load_source
        self.enabled = enabled
        self._wake, self._stop = Event(), Event()
        self._thread = None

    def start(self):
        self._thread = Thread(target=self._run, name="human-chat-memory", daemon=False)
        self._thread.start()

    def notify(self):
        self._wake.set()

    def process_one(self):
        if not self.enabled:
            return False
        job = self.jobs.next()
        if job is None:
            return False
        try:
            if job["user_id"] != self.service.user_id:
                raise ValueError("Maintenance job belongs to another user")
            if job["plan"]:
                plan = FormationPlan.model_validate_json(job["plan"])
            else:
                plan = self.formation.prepare(self.load_source(job), job["id"])
                self.jobs.save_plan(job["id"], plan)
                job["plan"] = plan.model_dump_json()
            if self.jobs.is_done(job["id"]):
                return True
            outcomes = [self.service.apply(operation).outcome for operation in plan.operations]
            self.jobs.finish(job["id"], ",".join(outcomes) if outcomes else "no_supported_change")
            if plan.diagnostics:
                logger.info("Memory job %s deferred/rejected %s candidates", job["id"], len(plan.diagnostics))
        except Exception:
            logger.exception("Memory job %s failed", job["id"])
            self.jobs.failed(job)
        return True

    def _run(self):
        while not self._stop.is_set():
            processed = self.process_one()
            if not processed:
                self._wake.wait(5)
                self._wake.clear()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join()
