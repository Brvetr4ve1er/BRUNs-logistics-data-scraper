"""In-memory job tracker for HTMX progress polling.

Why in-memory: progress polling is a 1-Hz read on a per-request basis. SQLite
would work, but RAM is fine for a single-process Flask app and avoids contention
with the extraction pipeline writing to the same DB.

Why a thread (not asyncio): Flask runs sync, the pipeline is sync, and we want
to return immediately from POST /upload. A daemon thread per job is plenty for
a single-user desktop tool. Replace with a real queue (RQ, Huey, Celery) if
this ever needs multi-user concurrency.

Public API:
    submit_job(file_path, module, doc_type) -> job_id  # starts thread, returns immediately
    get_job(job_id) -> Job | None                       # for polling endpoint
    list_recent(limit=20) -> list[Job]                  # for a future "history" page
    purge_old(max_age_seconds=3600)                     # called on every access
"""
from __future__ import annotations

import threading
import traceback
from datetime import datetime, timedelta
from typing import Optional

from core.pipeline.job import Job, JobStatus
from core.pipeline.router import route_file


# Thread-safe in-memory store. Cap at 200 entries; oldest evicted on overflow.
_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()
_MAX_JOBS = 200


def _purge_old_locked(max_age_seconds: int = 3600) -> None:
    """Caller must hold _LOCK. Drop TERMINATED jobs older than `max_age_seconds`.

    A job is only eligible for purge once it has a completed_at timestamp — an
    in-flight job that legitimately runs longer than max_age_seconds must not be
    evicted mid-execution (that would strand the UI's status poller on a 404).
    """
    cutoff = datetime.utcnow() - timedelta(seconds=max_age_seconds)
    stale = [jid for jid, j in _JOBS.items()
             if j.completed_at is not None and j.completed_at < cutoff]
    for jid in stale:
        _JOBS.pop(jid, None)
    # Hard cap — if still over the limit, drop the oldest COMPLETED jobs only,
    # never a running one.
    if len(_JOBS) > _MAX_JOBS:
        completed = sorted(
            ((jid, j) for jid, j in _JOBS.items() if j.completed_at is not None),
            key=lambda kv: kv[1].completed_at,
        )
        for jid, _ in completed[: len(_JOBS) - _MAX_JOBS]:
            _JOBS.pop(jid, None)


def _run_pipeline(job: Job, file_path: str, module: str, doc_type: str) -> None:
    """Worker body. Runs in a daemon thread."""
    try:
        result = route_file(file_path, module, doc_type)
        # `route_file` returns its own Job; merge its state into ours so the
        # ID surfaced to the UI stays stable.
        with _LOCK:
            job.status        = result.status
            job.result_data   = result.result_data
            job.error_message = result.error_message
            job.completed_at  = result.completed_at or datetime.utcnow()
            job.logs.extend(result.logs)

        # ── Domain projection: write to persons/documents_travel or
        # shipments/containers so the dashboards actually populate.
        # The engine pipeline only writes to the generic `documents` table.
        if result.status == "COMPLETED" and isinstance(result.result_data, dict):
            try:
                from core.api import projections
                from core.pipeline.router import get_db_path
                doc_id = result.result_data.get("document_id")
                ed = result.result_data.get("extracted_data") or {}
                ed["_source_file"] = file_path
                summary = projections.project(module, get_db_path(module), doc_id, ed)
                with _LOCK:
                    job.logs.append(f"[projection] {summary}")
            except Exception as proj_err:
                with _LOCK:
                    job.logs.append(f"[projection] WARNING: {type(proj_err).__name__}: {proj_err}")
    except Exception as e:
        with _LOCK:
            job.fail(f"{type(e).__name__}: {e}")
            job.logs.append(traceback.format_exc())


def submit_job(file_path: str, module: str, doc_type: str = "UNKNOWN") -> str:
    """Create a Job, register it, kick off a thread. Returns job_id immediately."""
    job = Job(
        type="DOCUMENT_EXTRACTION",
        input_data={"file_path": file_path, "module": module, "doc_type": doc_type},
    )
    job.status = JobStatus.PENDING
    job.log(f"Submitted: {file_path}")

    with _LOCK:
        _purge_old_locked()
        _JOBS[job.id] = job

    t = threading.Thread(
        target=_run_pipeline,
        args=(job, file_path, module, doc_type),
        daemon=True,
        name=f"job-{job.id[:8]}",
    )
    t.start()
    return job.id


def get_job(job_id: str) -> Optional[Job]:
    with _LOCK:
        _purge_old_locked()
        return _JOBS.get(job_id)


def list_recent(limit: int = 20) -> list[Job]:
    with _LOCK:
        _purge_old_locked()
        ordered = sorted(
            _JOBS.values(),
            key=lambda j: j.completed_at or j.created_at,
            reverse=True,
        )
        return ordered[:limit]


def progress_percent(job: Job) -> int:
    """Heuristic completion estimate based on status + log line count.

    The real pipeline doesn't expose a step counter, so we infer from the
    statuses it logs. Adjust mapping if processor.py adds new milestones.
    """
    if job.status == JobStatus.COMPLETED:
        return 100
    if job.status == JobStatus.FAILED:
        return 100
    if job.status == JobStatus.PENDING:
        return 5

    # PROCESSING — guess from log lines
    n = len(job.logs)
    if n <= 1: return 10
    if n <= 3: return 25
    if n <= 6: return 50
    if n <= 9: return 75
    return 90
