import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.pagination import decode_cursor, encode_cursor
from geopoc.api.schemas import (
    JobCreate,
    JobDetail,
    JobError,
    JobEventOut,
    JobKind,
    JobOut,
    JobPage,
    JobStatus,
    RecentJob,
    RecentJobPage,
    RunOut,
)
from geopoc.config import get_settings
from geopoc.db.models import Asset, Job, JobEvent, PipelineRun, Project
from geopoc.jobs import queue
from geopoc.registry.manifest import load_manifest, weights_problem
from geopoc.video.sampling import FRAME_MODEL_PIPELINES, SamplingError, SamplingPolicy

router = APIRouter(prefix="/api/v1", tags=["jobs"], responses=problem_responses(422))

# Pipelines that exist. The overhead pipeline is not implemented and is refused, not faked.
IMPLEMENTED_PIPELINES = {"ground_image", "video"}
EVENTS_SHOWN = 50


def job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        project_id=job.project_id,
        asset_id=job.asset_id,
        kind=job.kind,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        cancel_requested=job.cancel_requested,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        payload=job.payload,
        result=job.result,
        error=JobError(code=job.error_code, message=job.error_message) if job.error_code else None,
        has_partial_outputs=job.has_partial_outputs,
        retry_of_job_id=job.retry_of_job_id,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _project(session: Session, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


def _job(session: Session, job_id: uuid.UUID) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.kind not in ("preview", "analyze"):
        raise ProblemError(404, "JOB_NOT_FOUND", "Job not found")
    return job


def _check_analysis(session: Session, body: JobCreate, asset: Asset) -> dict:
    if body.pipeline is None or body.model is None:
        raise ProblemError(
            422, "VALIDATION_ERROR", "Request validation failed", "An analysis job needs a pipeline and a model."
        )
    if body.pipeline not in IMPLEMENTED_PIPELINES:
        raise ProblemError(
            422,
            "PIPELINE_NOT_AVAILABLE",
            "This analysis pipeline is not available",
            "Only the ground-image and video-frame pipelines exist. Overhead analysis is not implemented.",
        )
    if asset.modality != body.pipeline:
        raise ProblemError(
            422,
            "PIPELINE_INCOMPATIBLE",
            "This pipeline cannot analyse this asset",
            f"The {body.pipeline} pipeline does not accept a {asset.modality} asset.",
        )
    if body.pipeline == "video":
        if body.sampling is None:
            raise ProblemError(
                422,
                "SAMPLING_REQUIRED",
                "Choose how the video is sampled",
                "A video analysis needs a sampling interval and a maximum number of frames. Nothing is chosen for you.",
            )
    elif body.sampling is not None:
        raise ProblemError(422, "SAMPLING_NOT_APPLICABLE", "Sampling applies to video analysis only")
    # A frame is an image, so the existing image detectors are the models a video analysis may use.
    allowed = FRAME_MODEL_PIPELINES if body.pipeline == "video" else {body.pipeline}
    entry = load_manifest().get(body.model.name, body.model.version)
    if entry is None or entry.pipeline not in allowed:
        raise ProblemError(
            422, "MODEL_UNKNOWN", "Unknown model", "The model is not in the model manifest for this pipeline."
        )
    problem = weights_problem(entry)
    if problem:
        raise ProblemError(409, "MODEL_UNAVAILABLE", "This model cannot be run", problem)
    payload: dict = {"pipeline": body.pipeline, "model": {"name": entry.name, "version": entry.version}}
    if body.pipeline == "video":
        settings = get_settings()
        policy = SamplingPolicy(
            interval_s=body.sampling.interval_s,
            max_frames=body.sampling.max_frames,
            start_s=body.sampling.start_s,
            end_s=body.sampling.end_s,
        )
        try:
            policy.validate(
                min_interval_s=settings.video_analysis_min_interval_s, frames_ceiling=settings.video_analysis_max_frames
            )
        except SamplingError as exc:
            raise ProblemError(422, exc.code, "This sampling is not allowed", str(exc)) from exc
        payload["sampling"] = policy.as_dict()
    return payload


@router.post(
    "/projects/{project_id}/jobs",
    response_model=JobOut,
    status_code=202,
    responses={201: {"model": JobOut}, **problem_responses(400, 401, 404, 409)},
)
def create_job(
    project_id: uuid.UUID, body: JobCreate, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep
):
    """Queue background work on an asset. Returns at once; follow the job to see it run."""
    project = _project(session, project_id)
    if project.archived_at is not None:
        raise ProblemError(409, "PROJECT_ARCHIVED", "This project is archived", "Restore it before running jobs.")
    asset = session.get(Asset, body.asset_id)
    if asset is None or asset.project_id != project.id:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found in this project")

    payload = _check_analysis(session, body, asset) if body.kind == "analyze" else {}
    active = session.scalars(
        select(Job.id).where(
            Job.asset_id == asset.id, Job.kind == body.kind, Job.status.in_(queue.ACTIVE), Job.payload == payload
        )
    ).first()
    if active:
        raise ProblemError(409, "JOB_ALREADY_ACTIVE", "The same job is already queued or running", f"See job {active}.")

    job = queue.enqueue(
        session,
        kind=body.kind,
        project_id=project.id,
        asset_id=asset.id,
        payload=payload,
        created_by=profile.id,
        submit=body.submit,
        # Inference is not retried automatically: a model that fails on an image will fail again.
        max_attempts=3 if body.kind == "preview" else 2,
    )
    record_audit(
        session,
        actor=profile,
        action="job.create",
        entity_type="job",
        entity_id=job.id,
        request_id=request_id,
        details={"kind": body.kind, "asset_id": str(asset.id), **payload},
    )
    session.commit()
    return JSONResponse(job_out(job).model_dump(mode="json"), status_code=202 if body.submit else 201)


@router.get("/projects/{project_id}/jobs", response_model=JobPage, responses=problem_responses(404))
def list_jobs(
    project_id: uuid.UUID,
    session: SessionDep,
    asset_id: uuid.UUID | None = None,
    kind: JobKind | None = None,
    status: JobStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
):
    _project(session, project_id)
    stmt = select(Job).where(Job.project_id == project_id, Job.kind.in_(("preview", "analyze")))
    if asset_id:
        stmt = stmt.where(Job.asset_id == asset_id)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    if status:
        stmt = stmt.where(Job.status == status)
    if cursor:
        stmt = stmt.where(tuple_(Job.created_at, Job.id) < decode_cursor(cursor))
    rows = list(session.scalars(stmt.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit + 1)).all())
    page = rows[:limit]
    return JobPage(
        items=[job_out(j) for j in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None,
    )


@router.get("/jobs", response_model=RecentJobPage)
def recent_jobs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    status: JobStatus | None = None,
):
    """The most recent jobs across all projects, newest first, with the project and file they belong to."""
    stmt = (
        select(Job, Project.name, Asset.original_filename)
        .join(Project, Job.project_id == Project.id)
        .outerjoin(Asset, Job.asset_id == Asset.id)
        .where(Job.kind.in_(("preview", "analyze")))
    )
    if status:
        stmt = stmt.where(Job.status == status)
    rows = session.execute(stmt.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)).all()
    return RecentJobPage(
        items=[
            RecentJob(**job_out(job).model_dump(), project_name=name, asset_filename=filename)
            for job, name, filename in rows
        ]
    )


@router.get("/jobs/{job_id}", response_model=JobDetail, responses=problem_responses(404))
def get_job(job_id: uuid.UUID, session: SessionDep):
    job = _job(session, job_id)
    events = session.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.id.desc()).limit(EVENTS_SHOWN)
    ).all()
    return JobDetail(
        **job_out(job).model_dump(),
        events=[JobEventOut(at=e.at, level=e.level, stage=e.stage, message=e.message) for e in reversed(events)],
    )


def _act(session: Session, job: Job, profile, request_id: str, action: str) -> JobOut:
    record_audit(session, actor=profile, action=action, entity_type="job", entity_id=job.id, request_id=request_id)
    session.commit()
    return job_out(job)


@router.post(
    "/jobs/{job_id}/cancel", response_model=JobOut, status_code=202, responses=problem_responses(401, 404, 409)
)
def cancel_job(job_id: uuid.UUID, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep):
    """Ask for a job to stop. A job that has not started is cancelled at once; a running one stops shortly."""
    job = _job(session, job_id)
    try:
        queue.request_cancel(session, job)
    except queue.JobStateError as exc:
        raise ProblemError(409, exc.code, "The job cannot be cancelled", str(exc)) from exc
    return _act(session, job, profile, request_id, "job.cancel")


@router.post("/jobs/{job_id}/start", response_model=JobOut, status_code=202, responses=problem_responses(401, 404, 409))
def start_job(job_id: uuid.UUID, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep):
    """Submit a draft job."""
    job = _job(session, job_id)
    try:
        queue.submit_draft(session, job)
    except queue.JobStateError as exc:
        raise ProblemError(409, exc.code, "The job cannot be started", str(exc)) from exc
    return _act(session, job, profile, request_id, "job.start")


@router.post("/jobs/{job_id}/retry", response_model=JobOut, status_code=202, responses=problem_responses(401, 404, 409))
def retry_job(job_id: uuid.UUID, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep):
    """Run a failed or cancelled job again as a new job. The old job and its log are kept."""
    job = _job(session, job_id)
    if job.status not in ("failed", "cancelled"):
        raise ProblemError(
            409, "JOB_NOT_RETRYABLE", "Only a failed or cancelled job can be retried", f"This job is {job.status}."
        )
    new = queue.enqueue(
        session,
        kind=job.kind,
        project_id=job.project_id,
        asset_id=job.asset_id,
        payload=job.payload,
        created_by=profile.id,
        max_attempts=job.max_attempts,
        retry_of=job.id,
    )
    return _act(session, new, profile, request_id, "job.retry")


@router.get("/runs/{run_id}", response_model=RunOut, responses=problem_responses(404))
def get_run(run_id: uuid.UUID, session: SessionDep):
    """How an analysis was produced: model, version, weights checksum, settings, device, timings."""
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise ProblemError(404, "RUN_NOT_FOUND", "Run not found")
    return RunOut(**{column.name: getattr(run, column.name) for column in PipelineRun.__table__.columns})
