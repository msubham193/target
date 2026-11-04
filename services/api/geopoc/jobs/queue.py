"""The job queue: rows in the `job` table, claimed with SELECT ... FOR UPDATE SKIP LOCKED.

States are those of the specification (V2 section 9): draft, queued, validating, processing, needs_review,
completed, completed_with_warnings, failed, cancelled.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from geopoc.config import get_settings
from geopoc.db.models import Job, JobEvent, utcnow

RUNNING = ("validating", "processing")
ACTIVE = ("queued", *RUNNING)
TERMINAL = ("needs_review", "completed", "completed_with_warnings", "failed", "cancelled")


class JobStateError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def add_event(session: Session, job: Job, message: str, level: str = "info") -> None:
    session.add(JobEvent(job_id=job.id, level=level, stage=job.stage, message=message[:2000]))


def enqueue(
    session: Session,
    *,
    kind: str,
    project_id: uuid.UUID | None,
    asset_id: uuid.UUID | None,
    payload: dict[str, Any] | None = None,
    created_by: uuid.UUID | None = None,
    submit: bool = True,
    max_attempts: int = 3,
    retry_of: uuid.UUID | None = None,
) -> Job:
    """Add a job. The caller commits, so a job can be created in the same transaction as what it works on."""
    job = Job(
        kind=kind,
        project_id=project_id,
        asset_id=asset_id,
        payload=payload or {},
        created_by=created_by,
        status="queued" if submit else "draft",
        max_attempts=max_attempts,
        retry_of_job_id=retry_of,
    )
    session.add(job)
    session.flush()
    add_event(session, job, "Queued." if submit else "Saved as a draft; not submitted.")
    return job


def submit_draft(session: Session, job: Job) -> None:
    if job.status != "draft":
        raise JobStateError("JOB_NOT_DRAFT", "Only a draft job can be started.")
    job.status = "queued"
    job.run_after = utcnow()
    add_event(session, job, "Submitted.")


def claim_next(session: Session, worker_id: str) -> Job | None:
    """Take the oldest runnable queued job, or None. Safe with several workers."""
    job = session.scalars(
        select(Job)
        .where(Job.status == "queued", Job.run_after <= utcnow())
        .order_by(Job.created_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()
    if job is None:
        session.rollback()
        return None
    now = utcnow()
    job.status = "validating"
    job.stage = "starting"
    job.progress = None
    job.attempt += 1
    job.worker_id = worker_id
    job.heartbeat_at = now
    job.started_at = job.started_at or now
    job.error_code = job.error_message = None
    add_event(session, job, f"Started (attempt {job.attempt} of {job.max_attempts}).")
    session.commit()
    return job


def heartbeat(session: Session, job_id: uuid.UUID) -> bool:
    """Record that the job's worker is alive. Returns whether cancellation has been requested."""
    row = session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_(RUNNING))
        .values(heartbeat_at=utcnow())
        .returning(Job.cancel_requested)
    ).first()
    session.commit()
    return bool(row and row[0])


def finish(
    session: Session,
    job: Job,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    has_partial_outputs: bool = False,
    message: str | None = None,
) -> None:
    assert status in TERMINAL
    job.status = status
    job.finished_at = utcnow()
    job.result = result
    job.error_code = error_code
    job.error_message = error_message
    job.has_partial_outputs = has_partial_outputs
    if status != "failed" and status != "cancelled":
        job.progress = 1.0
    add_event(session, job, message or f"Finished: {status}.", level="error" if status == "failed" else "info")


def retry_or_fail(session: Session, job: Job, code: str, message: str) -> str:
    """After a transient failure: queue another attempt if any remain, otherwise fail. Returns the new status."""
    if job.attempt < job.max_attempts:
        delay = get_settings().job_retry_backoff_s * job.attempt
        job.status = "queued"
        job.stage = None
        job.progress = None
        job.worker_id = None
        job.run_after = utcnow() + timedelta(seconds=delay)
        job.error_code, job.error_message = code, message
        add_event(session, job, f"{message} Retrying in {delay:.0f} s ({code}).", level="warning")
    else:
        finish(
            session,
            job,
            "failed",
            error_code=code,
            error_message=message,
            message=f"{message} No attempts left ({code}).",
        )
    return job.status


def request_cancel(session: Session, job: Job) -> None:
    """Cancel now if the job has not started; otherwise ask the worker to stop it."""
    if job.status in ("draft", "queued"):
        finish(session, job, "cancelled", message="Cancelled before it started. No outputs exist.")
        job.cancel_requested = True
    elif job.status in RUNNING:
        if not job.cancel_requested:
            job.cancel_requested = True
            add_event(session, job, "Cancellation requested.")
    else:
        raise JobStateError("JOB_ALREADY_FINISHED", f"The job has already finished ({job.status}).")


def recover_stale(session: Session) -> int:
    """Requeue or fail running jobs whose worker stopped reporting (a crash or a restart)."""
    cutoff = utcnow() - timedelta(seconds=get_settings().job_stale_after_s)
    stale = session.scalars(
        select(Job).where(Job.status.in_(RUNNING), Job.heartbeat_at < cutoff).with_for_update(skip_locked=True)
    ).all()
    for job in stale:
        if job.cancel_requested:
            finish(session, job, "cancelled", message="Cancelled; its worker had stopped. No outputs exist.")
        else:
            retry_or_fail(session, job, "WORKER_LOST", "The worker running this job stopped responding.")
    session.commit()
    return len(stale)
