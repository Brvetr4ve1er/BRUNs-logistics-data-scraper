"""Regression tests for the third batch of audit fixes (infra / leaks / SSRF).

Covers: the shipments.tan migration index, job-purge never evicting a running
job, and the LLM SSRF target guard. No Ollama / network required.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_migration_creates_shipments_tan_index(tmp_path):
    from core.storage.db import init_schema, get_connection
    from core.storage.migrations import run_migrations

    db = str(tmp_path / "logistics.db")
    init_schema(db)
    run_migrations(db)
    conn = get_connection(db)
    try:
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_shipments_tan'"
        ).fetchone()
    finally:
        conn.close()
    assert idx is not None, "migration 006 must create idx_shipments_tan"


def test_migrations_are_idempotent(tmp_path):
    from core.storage.db import init_schema
    from core.storage.migrations import run_migrations

    db = str(tmp_path / "logistics.db")
    init_schema(db)
    first = run_migrations(db)
    second = run_migrations(db)
    assert first, "first run should apply migrations"
    assert second == [], "second run must be a no-op"


def test_purge_never_evicts_running_job():
    from core.api import job_tracker
    from core.pipeline.job import Job

    with job_tracker._LOCK:
        job_tracker._JOBS.clear()
        # A running job created 2h ago (completed_at is None)
        running = Job(type="DOCUMENT_EXTRACTION")
        running.created_at = datetime.utcnow() - timedelta(hours=2)
        running.completed_at = None
        # A finished job completed 2h ago
        done = Job(type="DOCUMENT_EXTRACTION")
        done.completed_at = datetime.utcnow() - timedelta(hours=2)
        job_tracker._JOBS["run-1"] = running
        job_tracker._JOBS["done-1"] = done

        job_tracker._purge_old_locked(max_age_seconds=3600)

        assert "run-1" in job_tracker._JOBS, "running job must survive purge"
        assert "done-1" not in job_tracker._JOBS, "old completed job should be purged"
        job_tracker._JOBS.clear()


def test_llm_ssrf_guard_blocks_link_local_and_bad_scheme():
    from core.api.server import _validate_llm_target
    assert _validate_llm_target({"base_url": "http://localhost"})[0] is True
    assert _validate_llm_target({"base_url": "http://127.0.0.1"})[0] is True
    assert _validate_llm_target({"base_url": "http://169.254.169.254"})[0] is False
    assert _validate_llm_target({"base_url": "ftp://example.com"})[0] is False
    assert _validate_llm_target({"base_url": "file:///etc/passwd"})[0] is False
