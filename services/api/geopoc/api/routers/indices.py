"""Derived spectral indices: which indices exist, which locally stored scenes can feed one, and runs of an index as
background jobs.

A run is an ordinary `derived_index` job in the existing queue: the worker computes the index, and the result is stored
as a derived raster asset that the existing asset, layer and tile routes then serve. The folder searched is set on the
server; a client never supplies a path, a URL or a file name, and nothing returned carries a location.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.routers.imagery import SettingsDep
from geopoc.api.schemas import (
    IndexesOut,
    IndexInputBandOut,
    IndexInputsOut,
    IndexOut,
    IndexRunCreate,
    IndexRunInput,
    IndexRunOut,
    IndexRunResult,
    IndexRunsOut,
    IndexSceneOut,
    JobError,
)
from geopoc.db.models import Job, Project
from geopoc.geo import bandscan, colormaps
from geopoc.geo.indices import INDICES, IndexSpec
from geopoc.jobs import queue

router = APIRouter(prefix="/api/v1", tags=["indices"], responses=problem_responses(422))
KIND = "derived_index"
RUNS_SHOWN = 50


def _project(session: Session, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


def _spec(index_key: str) -> IndexSpec:
    spec = INDICES.get(index_key)
    if spec is None:
        raise ProblemError(404, "INDEX_NOT_FOUND", "Unknown index", f"Available: {', '.join(INDICES)}.")
    return spec


def _display(spec: IndexSpec) -> dict | None:
    return colormaps.describe(spec.colormap) if spec.colormap else None


@router.get("/indices", response_model=IndexesOut, responses=problem_responses(401))
def list_indices(profile: ProfileDep):
    """The spectral indices this server can compute. Local information only."""
    return IndexesOut(
        indices=[
            IndexOut(
                key=spec.key,
                title=spec.title,
                formula=spec.formula,
                inputs=[
                    {"role": spec.first_role, "band": spec.first_band},
                    {"role": spec.second_role, "band": spec.second_band},
                ],
                output="A float32 GeoTIFF on the inputs' own grid, with an explicit nodata value.",
                analytical_range=list(spec.analytical_range) if spec.analytical_range else None,
                display=_display(spec),
            )
            for spec in INDICES.values()
        ]
    )


@router.get(
    "/projects/{project_id}/indices/{index_key}/inputs",
    response_model=IndexInputsOut,
    responses=problem_responses(401, 404),
)
def index_inputs(
    project_id: uuid.UUID, index_key: str, session: SessionDep, settings: SettingsDep, profile: ProfileDep
):
    """The locally stored scenes that have the bands this index needs, and whether each pair can be combined.

    Reads the product metadata and the headers of the band files in the server's band folder. Reflectance scaling is
    taken from each product's own metadata. Nothing is processed, copied or sent anywhere."""
    _project(session, project_id)
    spec = _spec(index_key)
    root = settings.imagery_band_root
    configured = root is not None and root.is_dir()
    scenes = bandscan.scan(root, spec)
    return IndexInputsOut(
        index=spec.key,
        configured=configured,
        scenes=[
            IndexSceneOut(
                scene_id=s.scene_id,
                product_uri=s.product.product_uri if s.product else None,
                tile_id=s.product.tile_id if s.product else None,
                acquisition_start=s.product.start_time if s.product else None,
                processing_baseline=s.product.processing_baseline if s.product else None,
                spacecraft=s.product.spacecraft if s.product else None,
                resolution_m=10,
                bands=[
                    IndexInputBandOut(
                        role=b.role,
                        band=b.band,
                        file_name=b.file_name,
                        found=b.found,
                        readable=b.readable,
                        width=b.width,
                        height=b.height,
                        bands=b.bands,
                        dtype=b.dtype,
                        crs=b.crs,
                        pixel_size_m=b.pixel_size,
                        scaling=b.scaling,
                        problems=b.problems,
                    )
                    for b in s.bands
                ],
                compatible=s.compatible,
                problems=s.problems,
            )
            for s in scenes
        ],
        note=(
            "No band folder is configured on the server, so no scenes are offered."
            if not configured
            else "Scenes found in the server's band folder. Nothing has been processed."
        ),
    )


# --- runs (background jobs) ---------------------------------------------------------------------------------------


def _run_out(job: Job) -> IndexRunOut:
    spec = INDICES.get(job.payload.get("index", ""))
    result = None
    if job.result and job.result.get("derived_asset_id"):
        try:
            result = IndexRunResult.model_validate(job.result)
        except ValidationError:  # a result written by a different version: shown as absent, never guessed
            result = None
    return IndexRunOut(
        job_id=job.id,
        index=job.payload.get("index", ""),
        scene_id=job.payload.get("scene_id", ""),
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        cancel_requested=job.cancel_requested,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=JobError(code=job.error_code, message=job.error_message) if job.error_code else None,
        inputs=[IndexRunInput(**item) for item in job.payload.get("inputs", [])],
        range_mask=job.payload.get("range_mask"),
        asset_id=job.asset_id,
        result=result,
        display=_display(spec) if spec else None,
    )


def _run(session: Session, project_id: uuid.UUID, index_key: str, job_id: uuid.UUID) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.kind != KIND or job.project_id != project_id or job.payload.get("index") != index_key:
        raise ProblemError(404, "RUN_NOT_FOUND", "Run not found")
    return job


@router.post(
    "/projects/{project_id}/indices/{index_key}/runs",
    response_model=IndexRunOut,
    status_code=202,
    responses=problem_responses(400, 401, 404, 409, 422),
)
def start_run(
    project_id: uuid.UUID,
    index_key: str,
    body: IndexRunCreate,
    session: SessionDep,
    settings: SettingsDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
):
    """Calculate the index for a scene in the background. The inputs are re-checked here, and a second run of the same
    scene is refused while one is queued or running. The result is stored as a derived raster asset."""
    project = _project(session, project_id)
    spec = _spec(index_key)
    if project.archived_at is not None:
        raise ProblemError(409, "PROJECT_ARCHIVED", "This project is archived", "Restore it before processing.")
    scene = bandscan.find_scene(settings.imagery_band_root, spec, body.scene_id)
    if scene is None:
        raise ProblemError(
            404, "SCENE_NOT_FOUND", "Scene not found", "It is not in the server's band folder. List the inputs again."
        )
    if not scene.compatible:
        reasons = scene.problems + [p for band in scene.bands for p in band.problems]
        raise ProblemError(
            422,
            "INPUTS_INCOMPATIBLE",
            "These bands cannot be combined",
            " ".join(reasons) or "The bands are missing or unreadable.",
            errors=[{"field": "scene_id", "message": r} for r in reasons],
        )
    # Serialise the duplicate check for this project and scene, so two requests cannot both pass it.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{KIND}:{project_id}:{scene.scene_id}"},
    )
    active = session.scalars(
        select(Job).where(
            Job.kind == KIND,
            Job.project_id == project_id,
            Job.status.in_(queue.ACTIVE),
            Job.payload["scene_id"].as_string() == scene.scene_id,
            Job.payload["index"].as_string() == spec.key,
        )
    ).first()
    if active is not None:
        raise ProblemError(
            409,
            "RUN_ALREADY_ACTIVE",
            "This scene is already being processed",
            f"Run {active.id} is {active.status}. Wait for it to finish or cancel it first.",
        )
    payload = {
        "index": spec.key,
        "scene_id": scene.scene_id,
        "range_mask": list(spec.analytical_range) if spec.analytical_range else None,
        "inputs": [{"role": b.role, "band": b.band, "file_name": b.file_name} for b in scene.bands],
    }
    job = queue.enqueue(
        session, kind=KIND, project_id=project_id, asset_id=None, payload=payload, created_by=profile.id
    )
    record_audit(
        session,
        actor=profile,
        action="job.derived_index",
        entity_type="job",
        entity_id=job.id,
        request_id=request_id,
        details={"project_id": str(project_id), **payload},
    )
    session.commit()
    return _run_out(job)


@router.get(
    "/projects/{project_id}/indices/{index_key}/runs",
    response_model=IndexRunsOut,
    responses=problem_responses(401, 404),
)
def list_runs(
    project_id: uuid.UUID,
    index_key: str,
    session: SessionDep,
    profile: ProfileDep,
    limit: Annotated[int, Query(ge=1, le=RUNS_SHOWN)] = 20,
):
    """Runs of this index in this project, newest first, with live status and, once finished, the result."""
    _project(session, project_id)
    spec = _spec(index_key)
    jobs = session.scalars(
        select(Job)
        .where(Job.kind == KIND, Job.project_id == project_id, Job.payload["index"].as_string() == spec.key)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(limit)
    ).all()
    return IndexRunsOut(runs=[_run_out(j) for j in jobs])


@router.get(
    "/projects/{project_id}/indices/{index_key}/runs/{job_id}",
    response_model=IndexRunOut,
    responses=problem_responses(401, 404),
)
def get_run(project_id: uuid.UUID, index_key: str, job_id: uuid.UUID, session: SessionDep, profile: ProfileDep):
    """One run: its state, real progress, error if any, and the result once it has finished."""
    _project(session, project_id)
    _spec(index_key)
    return _run_out(_run(session, project_id, index_key, job_id))


@router.post(
    "/projects/{project_id}/indices/{index_key}/runs/{job_id}/cancel",
    response_model=IndexRunOut,
    status_code=202,
    responses=problem_responses(401, 404, 409),
)
def cancel_run(
    project_id: uuid.UUID,
    index_key: str,
    job_id: uuid.UUID,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
):
    """Ask a run to stop. One that has not started is cancelled at once; a running one stops at its next checkpoint
    and keeps nothing."""
    _project(session, project_id)
    _spec(index_key)
    job = _run(session, project_id, index_key, job_id)
    try:
        queue.request_cancel(session, job)
    except queue.JobStateError as exc:
        raise ProblemError(409, exc.code, "The run cannot be cancelled", str(exc)) from exc
    record_audit(
        session, actor=profile, action="job.cancel", entity_type="job", entity_id=job.id, request_id=request_id
    )
    session.commit()
    return _run_out(job)
