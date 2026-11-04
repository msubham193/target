"""Phase 3: the database-backed job queue and the worker process.

Tests marked "real worker" start `python -m geopoc.jobs.worker` as a separate process, exactly as it runs in use.
"""

import subprocess
import sys
import threading
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from geopoc.db.models import Job, JobEvent, WorkerHeartbeat, utcnow
from geopoc.db.session import get_engine
from geopoc.jobs import queue, run_job
from tests.conftest import API_DIR

TERMINAL = set(queue.TERMINAL)


def session() -> Session:
    return Session(get_engine(), expire_on_commit=False)


def add_selftest(max_attempts: int = 3, submit: bool = True, **payload) -> uuid.UUID:
    with session() as s:
        job = queue.enqueue(
            s,
            kind="selftest",
            project_id=None,
            asset_id=None,
            payload=payload,
            submit=submit,
            max_attempts=max_attempts,
        )
        s.commit()
        return job.id


def load(job_id: uuid.UUID) -> Job:
    with session() as s:
        return s.get(Job, job_id)


def events(job_id: uuid.UUID) -> list[str]:
    with session() as s:
        return list(s.scalars(select(JobEvent.message).where(JobEvent.job_id == job_id).order_by(JobEvent.id)))


def wait_for(job_id: uuid.UUID, predicate, timeout: float = 30.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = load(job_id)
        if predicate(job):
            return job
        time.sleep(0.1)
    raise AssertionError(f"timed out; job is {load(job_id).status} at stage {load(job_id).stage}")


def start_worker(*args: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "geopoc.jobs.worker", *args], cwd=API_DIR)


def stop_tree(process: subprocess.Popen) -> None:
    """End the worker and everything under it at once, as a crash or a forced shutdown would."""
    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    process.wait(timeout=30)


# --- persistence and state transitions --------------------------------------------------------------------------


def test_a_job_is_persisted_claimed_run_and_finished(run_jobs):
    job_id = add_selftest()
    queued = load(job_id)
    assert (queued.status, queued.attempt, queued.started_at, queued.finished_at) == ("queued", 0, None, None)

    with session() as s:
        claimed = queue.claim_next(s, "worker-a")
        assert claimed.id == job_id
    running = load(job_id)
    assert (running.status, running.attempt, running.worker_id) == ("validating", 1, "worker-a")
    assert running.heartbeat_at is not None and running.started_at is not None

    assert run_job.run(job_id) == "completed"
    done = load(job_id)
    assert (done.status, done.progress, done.error_code) == ("completed", 1.0, None)
    assert done.finished_at >= done.started_at
    assert done.result == {"attempts": 1}
    assert events(job_id) == ["Queued.", "Started (attempt 1 of 3).", "Stage: selftest.", "Finished: completed."]


def test_job_state_survives_new_database_connections(run_jobs):
    """The queue is the database: nothing about a job lives in a process."""
    job_id = add_selftest()
    get_engine().dispose()  # every existing connection is gone
    assert load(job_id).status == "queued"
    assert run_jobs() == ["completed"]
    get_engine().dispose()
    assert load(job_id).status == "completed"


def test_draft_job_is_not_run_until_submitted(run_jobs):
    job_id = add_selftest(submit=False)
    assert load(job_id).status == "draft"
    assert run_jobs() == []
    with session() as s:
        queue.submit_draft(s, s.get(Job, job_id))
        s.commit()
    assert run_jobs() == ["completed"]
    with session() as s, pytest.raises(queue.JobStateError):
        queue.submit_draft(s, s.get(Job, job_id))


def test_jobs_run_oldest_first_and_two_workers_never_take_the_same_job():
    first, second = add_selftest(), add_selftest()
    with session() as a, session() as b:
        # Worker A holds its claim open (uncommitted lock); worker B must skip that row, not wait for it.
        locked = a.scalars(
            select(Job)
            .where(Job.status == "queued")
            .order_by(Job.created_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        other = queue.claim_next(b, "worker-b")
        assert (locked.id, other.id) == (first, second)
        assert queue.claim_next(b, "worker-b") is None
        a.rollback()


def test_only_the_nine_specified_statuses_are_storable():
    job_id = add_selftest()
    with get_engine().begin() as conn, pytest.raises(Exception, match="ck_job_status"):
        conn.execute(text("UPDATE job SET status = 'paused' WHERE id = :id"), {"id": job_id})


# --- errors and retries ----------------------------------------------------------------------------------------------


def test_deterministic_failure_is_not_retried(run_jobs):
    job_id = add_selftest(fail="fatal")
    assert run_jobs() == ["failed"]
    job = load(job_id)
    assert (job.status, job.attempt, job.error_code) == ("failed", 1, "SELFTEST_FATAL")
    assert job.error_message and job.finished_at


def test_transient_failure_is_retried_after_a_delay_and_then_succeeds(run_jobs):
    job_id = add_selftest(fail="transient", fail_until_attempt=1)
    assert run_jobs() == ["queued"]  # back in the queue, but not runnable yet
    waiting = load(job_id)
    assert (waiting.status, waiting.attempt, waiting.error_code) == ("queued", 1, "SELFTEST_TRANSIENT")
    assert waiting.run_after > utcnow()
    assert run_jobs() == []

    time.sleep(0.4)
    assert run_jobs() == ["completed"]
    done = load(job_id)
    assert (done.status, done.attempt, done.error_code) == ("completed", 2, None)
    assert any("Retrying in" in message for message in events(job_id))


def test_retries_stop_at_the_attempt_limit(run_jobs):
    job_id = add_selftest(max_attempts=2, fail="transient")
    assert run_jobs() == ["queued"]
    time.sleep(0.4)
    assert run_jobs() == ["failed"]
    job = load(job_id)
    assert (job.status, job.attempt, job.error_code) == ("failed", 2, "SELFTEST_TRANSIENT")
    assert "No attempts left" in events(job_id)[-1]


def test_unexpected_error_fails_the_job_without_leaking_details(run_jobs):
    job_id = add_selftest(fail="bug")
    assert run_jobs() == ["failed"]
    job = load(job_id)
    assert (job.status, job.attempt, job.error_code) == ("failed", 1, "INTERNAL_ERROR")
    assert "self-test" not in job.error_message  # the exception text goes to the log, not to the user


# --- cancellation -----------------------------------------------------------------------------------------------------


def test_cancelling_a_queued_job_is_immediate_and_it_never_runs(run_jobs):
    job_id = add_selftest()
    with session() as s:
        queue.request_cancel(s, s.get(Job, job_id))
        s.commit()
    job = load(job_id)
    assert (job.status, job.has_partial_outputs) == ("cancelled", False)
    assert run_jobs() == []
    with session() as s, pytest.raises(queue.JobStateError) as caught:
        queue.request_cancel(s, s.get(Job, job_id))
    assert caught.value.code == "JOB_ALREADY_FINISHED"


def test_a_running_job_stops_at_its_next_checkpoint_when_cancelled():
    job_id = add_selftest(sleep_s=20)
    with session() as s:
        queue.claim_next(s, "worker-a")
    runner = threading.Thread(target=run_job.run, args=(job_id,))
    runner.start()
    wait_for(job_id, lambda j: j.status == "processing")
    started = time.monotonic()
    with session() as s:
        queue.request_cancel(s, s.get(Job, job_id))
        s.commit()
    runner.join(timeout=10)
    assert not runner.is_alive()
    assert time.monotonic() - started < 5  # it did not run its full 20 seconds
    job = load(job_id)
    assert (job.status, job.cancel_requested, job.has_partial_outputs) == ("cancelled", True, False)
    assert "Cancellation requested." in events(job_id)
    assert "Nothing from this attempt was kept" in events(job_id)[-1]


# --- stale-job recovery (logic) -----------------------------------------------------------------------------------------


def test_a_job_whose_worker_stopped_reporting_is_requeued(run_jobs):
    job_id = add_selftest()
    with session() as s:
        queue.claim_next(s, "dead-worker")
        assert queue.recover_stale(s) == 0  # it has only just been claimed
        s.execute(
            text("UPDATE job SET heartbeat_at = :t WHERE id = :id"),
            {"t": utcnow() - timedelta(minutes=5), "id": job_id},
        )
        s.commit()
        assert queue.recover_stale(s) == 1
    job = load(job_id)
    assert (job.status, job.attempt, job.error_code) == ("queued", 1, "WORKER_LOST")
    time.sleep(0.4)
    assert run_jobs() == ["completed"]
    assert load(job_id).attempt == 2


def test_a_lost_job_with_no_attempts_left_fails_and_a_cancelled_one_is_cancelled():
    exhausted, cancelled = add_selftest(max_attempts=1), add_selftest()
    with session() as s:
        queue.claim_next(s, "dead-worker")
        queue.claim_next(s, "dead-worker")
        s.execute(text("UPDATE job SET heartbeat_at = :t"), {"t": utcnow() - timedelta(minutes=5)})
        s.execute(text("UPDATE job SET cancel_requested = true WHERE id = :id"), {"id": cancelled})
        s.commit()
        assert queue.recover_stale(s) == 2
    assert (load(exhausted).status, load(exhausted).error_code) == ("failed", "WORKER_LOST")
    assert load(cancelled).status == "cancelled"


# --- the real worker process ----------------------------------------------------------------------------------------------


def test_real_worker_runs_queued_jobs_and_reports_itself():
    first, second = add_selftest(), add_selftest(fail="fatal")
    worker = start_worker("--exit-when-idle")
    assert worker.wait(timeout=120) == 0
    assert load(first).status == "completed"
    assert (load(second).status, load(second).error_code) == ("failed", "SELFTEST_FATAL")
    assert load(first).worker_id == load(second).worker_id != "test-worker"

    with session() as s:
        (beat,) = s.scalars(select(WorkerHeartbeat)).all()
    assert beat.worker_id == load(first).worker_id
    assert beat.capabilities["python"].startswith("3.")
    assert "cuda_available" in beat.capabilities and "ffmpeg" in beat.capabilities


def test_real_worker_retries_a_job_whose_process_crashed():
    job_id = add_selftest(fail="crash", fail_until_attempt=1)
    worker = start_worker("--max-jobs", "2")
    try:
        job = wait_for(job_id, lambda j: j.status in TERMINAL, timeout=120)
    finally:
        worker.wait(timeout=60)
    assert (job.status, job.attempt) == ("completed", 2)
    assert any(
        "ended unexpectedly (exit code 3)" in message and "WORKER_CHILD_CRASHED" in message
        for message in events(job_id)
    )


def test_real_worker_stops_a_job_that_ignores_cancellation():
    job_id = add_selftest(sleep_s=120, ignore_cancel=True)
    worker = start_worker("--max-jobs", "1")
    try:
        wait_for(job_id, lambda j: j.status == "processing", timeout=60)
        with session() as s:
            queue.request_cancel(s, s.get(Job, job_id))
            s.commit()
        requested = time.monotonic()
        job = wait_for(job_id, lambda j: j.status in TERMINAL, timeout=30)
        took = time.monotonic() - requested
    finally:
        if worker.poll() is None:
            worker.wait(timeout=60)
    assert job.status == "cancelled" and job.has_partial_outputs is False
    assert took < 15  # one second of grace, then its process is ended; not the 120 s it wanted
    assert "its process was stopped" in events(job_id)[-1]


def test_a_job_interrupted_by_a_worker_crash_is_finished_by_a_new_worker():
    """Process restart, performed for real: the worker is killed mid-job and a second worker takes over."""
    job_id = add_selftest(sleep_s=60, sleep_first_attempt_only=True)
    first = start_worker()
    try:
        wait_for(job_id, lambda j: j.status == "processing", timeout=60)
        first_worker_id = load(job_id).worker_id
    finally:
        stop_tree(first)

    # Nothing is running now. The job is still recorded as in progress, exactly as the crash left it.
    time.sleep(0.5)
    assert load(job_id).status == "processing"

    second = start_worker("--max-jobs", "1")
    try:
        job = wait_for(job_id, lambda j: j.status in TERMINAL, timeout=120)
    finally:
        second.wait(timeout=60)
    assert (job.status, job.attempt) == ("completed", 2)
    assert job.worker_id != first_worker_id
    assert any("stopped responding" in message and "WORKER_LOST" in message for message in events(job_id))
