"""The local worker: `python -m geopoc.jobs.worker`. One process, one job at a time, no broker.

It claims queued jobs from the database, runs each in a child process, reports that it is alive, stops a job
when cancellation is requested, and recovers jobs left behind by a worker that crashed or was restarted.
"""

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from geopoc.config import get_settings
from geopoc.db.models import Job, WorkerHeartbeat, utcnow
from geopoc.db.session import get_engine
from geopoc.geo import projenv
from geopoc.jobs import queue

logger = logging.getLogger("geopoc.worker")
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def probe_capabilities() -> dict:
    """Ask a short-lived process what this environment can run, so the worker itself never loads PyTorch."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "geopoc.jobs.probe"],
            capture_output=True,
            timeout=120,
            creationflags=_NO_WINDOW,
            check=False,
        )
        return json.loads(out.stdout.decode("utf-8", errors="replace").strip().splitlines()[-1])
    except Exception:  # a failed probe only means "capabilities unknown"
        return {"probe": "failed"}


def kill_tree(process: subprocess.Popen) -> None:
    """End a child and everything it started. On Windows the venv's python.exe is a launcher with its own child."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            creationflags=_NO_WINDOW,
            check=False,
        )
    else:
        process.kill()
    process.wait(timeout=30)


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.capabilities: dict = {}

    def beat(self, session: Session) -> None:
        now = utcnow()
        session.execute(
            insert(WorkerHeartbeat)
            .values(worker_id=self.worker_id, started_at=now, at=now, capabilities=self.capabilities)
            .on_conflict_do_update(index_elements=["worker_id"], set_={"at": now, "capabilities": self.capabilities})
        )
        session.commit()

    def run_one(self, session: Session, job: Job) -> None:
        logger.info("Running job %s (%s), attempt %d", job.id, job.kind, job.attempt)
        child = subprocess.Popen([sys.executable, "-m", "geopoc.jobs.run_job", str(job.id)], creationflags=_NO_WINDOW)
        kill_at: float | None = None
        killed = False
        while child.poll() is None:
            time.sleep(0.25)
            cancel = queue.heartbeat(session, job.id)
            self.beat(session)
            if cancel and kill_at is None:
                # The job notices the request itself at its next checkpoint; this is the backstop.
                kill_at = time.monotonic() + self.settings.job_cancel_grace_s
            if kill_at is not None and time.monotonic() > kill_at and not killed:
                logger.warning("Job %s did not stop in time; ending its process", job.id)
                kill_tree(child)
                killed = True

        session.expire_all()
        job = session.get(Job, job.id)
        if job.status in queue.RUNNING:  # the child ended without recording an outcome
            if job.cancel_requested:
                queue.finish(session, job, "cancelled", message="Cancelled; its process was stopped. No outputs exist.")
            else:
                queue.retry_or_fail(
                    session,
                    job,
                    "WORKER_CHILD_CRASHED",
                    f"The job's process ended unexpectedly (exit code {child.returncode}).",
                )
            session.commit()
        logger.info("Job %s -> %s", job.id, job.status)

    def _probe(self) -> None:
        self.capabilities = probe_capabilities()
        logger.info("Worker %s capabilities: %s", self.worker_id, self.capabilities)

    def run(self, max_jobs: int | None = None, exit_when_idle: bool = False) -> int:
        # Loading PyTorch to find out what the environment can run can take a minute on a cold start. Jobs run in
        # child processes, so the worker starts taking them at once and reports its capabilities when known.
        self.capabilities = {"probe": "running"}
        probe = threading.Thread(target=self._probe, name="capability-probe", daemon=True)
        probe.start()
        logger.info("Worker %s started; checking capabilities in the background.", self.worker_id)
        with Session(get_engine(), expire_on_commit=False) as session:
            done = self._loop(session, max_jobs, exit_when_idle)
            # A worker that stops on its own soon after starting still reports what it found.
            probe.join(timeout=150)
            self.beat(session)
        return done

    def _loop(self, session: Session, max_jobs: int | None, exit_when_idle: bool) -> int:
        done = 0
        while max_jobs is None or done < max_jobs:
            self.beat(session)
            recovered = queue.recover_stale(session)
            if recovered:
                logger.warning("Recovered %d job(s) left by a stopped worker", recovered)
            job = queue.claim_next(session, self.worker_id)
            if job is None:
                if exit_when_idle:
                    break
                time.sleep(self.settings.job_poll_interval_s)
                continue
            self.run_one(session, job)
            done += 1
        return done


def main() -> None:
    parser = argparse.ArgumentParser(description="Geospatial PoC background worker")
    parser.add_argument("--max-jobs", type=int, default=None, help="stop after this many jobs")
    parser.add_argument("--exit-when-idle", action="store_true", help="stop when the queue is empty")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    projenv.report()  # logs a warning if the PROJ environment is not as it should be; never stops the worker
    try:
        Worker().run(max_jobs=args.max_jobs, exit_when_idle=args.exit_when_idle)
    except KeyboardInterrupt:
        # A job in flight is picked up again by the next worker through stale-job recovery.
        logger.info("Worker stopped.")


if __name__ == "__main__":
    main()
