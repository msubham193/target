"""Runs one claimed job to a terminal state: `python -m geopoc.jobs.run_job <job_id>`.

Started by the worker as a child process, so that a job can be stopped by ending the process and so that
everything it loaded (including GPU memory) is released when it exits.
"""

import logging
import sys
import uuid

from sqlalchemy.orm import Session

from geopoc.db.models import Job
from geopoc.db.session import get_engine
from geopoc.jobs import queue
from geopoc.jobs.handlers import HANDLERS, JobCancelled, JobContext, JobError, TransientJobError

logger = logging.getLogger("geopoc.jobs")

CANCELLED = "Cancelled at the worker's request. Nothing from this attempt was kept."


def run(job_id: uuid.UUID) -> str:
    with Session(get_engine(), expire_on_commit=False) as session:
        job = session.get(Job, job_id)
        if job is None or job.status not in queue.RUNNING:
            return "skipped"
        ctx = JobContext(session=session, job=job)
        try:
            handler = HANDLERS.get(job.kind)
            if handler is None:
                raise JobError("UNKNOWN_JOB_KIND", f"No handler exists for job kind {job.kind!r}.")
            outcome = handler(ctx)
            ctx.check_cancel()
            queue.finish(session, job, outcome.status, result=outcome.result, message=outcome.message)
        except JobCancelled:
            # Handlers write their outputs in one transaction at the end, so a cancelled attempt leaves none.
            session.rollback()
            ctx.run_discard()
            queue.finish(session, job, "cancelled", message=CANCELLED)
        except TransientJobError as exc:
            session.rollback()
            ctx.run_discard()
            queue.retry_or_fail(session, job, exc.code, str(exc))
        except JobError as exc:
            session.rollback()
            ctx.run_discard()
            queue.finish(session, job, "failed", error_code=exc.code, error_message=str(exc), message=str(exc))
        except Exception:
            session.rollback()
            ctx.run_discard()
            logger.exception("Job %s failed unexpectedly", job_id)
            queue.finish(
                session,
                job,
                "failed",
                error_code="INTERNAL_ERROR",
                error_message="The job failed unexpectedly. Details are in the worker log.",
            )
        session.commit()
        return job.status


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run(uuid.UUID(sys.argv[1]))
