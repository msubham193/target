"""What each kind of job does. A handler runs inside a child process started by the worker."""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from geopoc.db.models import Job
from geopoc.jobs import queue


class JobError(Exception):
    """A failure that will happen again if retried (bad input, unsupported file). Never retried automatically."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TransientJobError(JobError):
    """A failure that may not happen again (a resource was busy or missing for a moment). Retried with a delay."""


class JobCancelled(Exception):
    pass


@dataclass
class Outcome:
    status: str = "completed"
    result: dict[str, Any] = field(default_factory=dict)
    message: str | None = None


@dataclass
class JobContext:
    session: Session
    job: Job
    # Files a handler wrote outside the database. Run if the attempt ends without its results being saved, so a
    # rolled-back
    # job (cancelled, failed, retried) leaves nothing behind that no record points to.
    discard: list[Callable[[], None]] = field(default_factory=list)

    def run_discard(self) -> None:
        for action in self.discard:
            try:
                action()
            except OSError:
                pass
        self.discard.clear()

    def stage(self, name: str, progress: float | None = None, status: str | None = None) -> None:
        """Record where the job is. Also the point at which a requested cancellation takes effect."""
        self.check_cancel()
        self.job.stage = name
        self.job.progress = progress
        if status:
            self.job.status = status
        queue.add_event(self.session, self.job, f"Stage: {name}.")
        self.session.commit()

    def progress(self, fraction: float) -> None:
        self.check_cancel()
        self.job.progress = max(0.0, min(1.0, fraction))
        self.session.commit()

    def event(self, message: str, level: str = "info") -> None:
        queue.add_event(self.session, self.job, message, level)
        self.session.commit()

    def check_cancel(self) -> None:
        requested = self.session.scalar(select(Job.cancel_requested).where(Job.id == self.job.id))
        if requested:
            raise JobCancelled


def _selftest(ctx: JobContext) -> Outcome:
    """Diagnostic job used by automated tests to exercise the lifecycle. Not creatable through the API."""
    payload = ctx.job.payload
    ctx.stage("selftest", progress=0.0, status="processing")
    sleep_s = float(payload.get("sleep_s", 0))
    if payload.get("sleep_first_attempt_only") and ctx.job.attempt > 1:
        sleep_s = 0.0
    steps = max(1, int(sleep_s / 0.1))
    for step in range(steps):
        if sleep_s:
            time.sleep(0.1)
        if payload.get("ignore_cancel"):
            continue
        ctx.progress((step + 1) / steps)
    fail = payload.get("fail")
    if fail and ctx.job.attempt <= int(payload.get("fail_until_attempt", 10**6)):
        if fail == "fatal":
            raise JobError("SELFTEST_FATAL", "Deterministic failure requested by the self-test.")
        if fail == "transient":
            raise TransientJobError("SELFTEST_TRANSIENT", "Transient failure requested by the self-test.")
        if fail == "crash":
            os._exit(3)  # the process dies without reporting anything
        if fail == "bug":
            raise RuntimeError("unexpected error requested by the self-test")
    return Outcome(result={"attempts": ctx.job.attempt})


def _preview(ctx: JobContext) -> Outcome:
    from geopoc.previews.generate import run_preview_job

    return run_preview_job(ctx)


def _analyze(ctx: JobContext) -> Outcome:
    if ctx.job.payload.get("pipeline") == "video":
        from geopoc.pipelines.video_frames import run_video_analysis_job

        return run_video_analysis_job(ctx)
    from geopoc.pipelines.ground_image import run_analysis_job

    return run_analysis_job(ctx)


def _statistics(ctx: JobContext) -> Outcome:
    from geopoc.rastertools import run_statistics_job

    return run_statistics_job(ctx)


def _display_copy(ctx: JobContext) -> Outcome:
    from geopoc.rastertools import run_display_copy_job

    return run_display_copy_job(ctx)


def _compare(ctx: JobContext) -> Outcome:
    from geopoc.rastertools import run_compare_job

    return run_compare_job(ctx)


def _derived_index(ctx: JobContext) -> Outcome:
    from geopoc.derived_index import run_derived_index_job

    return run_derived_index_job(ctx)


HANDLERS: dict[str, Callable[[JobContext], Outcome]] = {
    "selftest": _selftest,
    "preview": _preview,
    "analyze": _analyze,
    "statistics": _statistics,
    "display_copy": _display_copy,
    "compare": _compare,
    "derived_index": _derived_index,
}
