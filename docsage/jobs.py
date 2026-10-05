"""In-process background jobs with progress, cancellation and restart recovery."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .errors import Cancelled, DocSageError
from .store import get_db
from .store.db import now

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, workers: int = 2, synchronous: bool = False):
        self.synchronous = synchronous
        self.pool = None if synchronous else ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    # -- cancellation --------------------------------------------------------------------------
    def cancel(self, job_id: str) -> None:
        db = get_db()
        job = db.get_job(job_id)
        if job["status"] not in ("queued", "running"):
            return
        with self._lock:
            self._cancelled.add(job_id)
        db.update_job(job_id, cancel_requested=1, message="Cancelling…")

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    # -- submission -----------------------------------------------------------------------------
    def submit(
        self,
        job: dict[str, Any],
        fn: Callable[[Callable[[float, str, str], None], Callable[[], bool]], dict[str, Any] | None],
    ) -> None:
        job_id = job["id"]

        def progress(fraction: float, stage: str, message: str) -> None:
            get_db().update_job(job_id, progress=round(fraction, 4), stage=stage, message=message)

        def run() -> None:
            db = get_db()
            if self.is_cancelled(job_id):
                db.update_job(
                    job_id, status="cancelled", finished_at=now(), message="Cancelled before start."
                )
                if job.get("document_id"):
                    db.update_document(
                        job["document_id"], status="cancelled", error="Cancelled before start."
                    )
                with self._lock:
                    self._cancelled.discard(job_id)
                return
            db.update_job(job_id, status="running", started_at=now(), stage="starting")
            try:
                result = fn(progress, lambda: self.is_cancelled(job_id))
                db.update_job(
                    job_id,
                    status="succeeded",
                    progress=1.0,
                    stage="done",
                    finished_at=now(),
                    result=result,
                    message="Completed",
                )
            except Cancelled:
                db.update_job(job_id, status="cancelled", finished_at=now(), message="Cancelled")
            except DocSageError as exc:
                db.update_job(
                    job_id, status="failed", finished_at=now(), message=exc.message, result={"hint": exc.hint}
                )
            except Exception as exc:  # pragma: no cover - unexpected
                log.exception("job %s failed", job_id)
                db.update_job(job_id, status="failed", finished_at=now(), message=f"Unexpected error: {exc}")
            finally:
                with self._lock:
                    self._cancelled.discard(job_id)

        if self.pool is None:
            run()
        else:
            self.pool.submit(run)

    def shutdown(self) -> None:
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)


_runner: JobRunner | None = None


def get_runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
    return _runner


def set_runner(runner: JobRunner) -> None:
    global _runner
    _runner = runner


def submit_ingest(document_id: str) -> dict[str, Any]:
    from .ingest.pipeline import ingest_document

    db = get_db()
    doc = db.get_document(document_id)
    active = [j for j in db.list_jobs(doc["collection_id"], active=True) if j["document_id"] == document_id]
    if active:
        return active[0]
    job = db.create_job(doc["collection_id"], "ingest", document_id)
    db.update_document(document_id, status="queued", error=None)

    def work(progress, is_cancelled):
        result = ingest_document(document_id, progress=progress, is_cancelled=is_cancelled)
        return {
            "chunks": result.chunks,
            "usage": result.usage,
            "warnings": result.warnings,
            "duration_s": result.duration_s,
        }

    get_runner().submit(job, work)
    return db.get_job(job["id"])
