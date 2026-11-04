"""Reading the plan and the results of video-frame analysis. Nothing here runs a model or starts a job.

Everything is scoped to a project and one of its videos: a video or run of another project is "not found". Opening a
result
only reads what the analysis job stored.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from geopoc.api.deps import SessionDep
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import (
    VideoFrameFailureOut,
    VideoFrameOut,
    VideoFramesOut,
    VideoSampling,
    VideoSamplingPlan,
)
from geopoc.config import get_settings
from geopoc.db.models import Artifact, Asset, ObservationRecord, PipelineRun, Project
from geopoc.pipelines.video_frames import COORDINATES, FRAME_ARTIFACT_KIND, FRAME_NUMBERING, PIPELINE
from geopoc.storage import store
from geopoc.video.sampling import POLICY_TEXT, SamplingError, SamplingPolicy, wanted_times

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/assets/{asset_id}/video-analysis",
    tags=["video-analysis"],
    responses=problem_responses(404, 409, 422),
)

NOTE = (
    "Model predictions on sampled frames, not confirmed findings. Scores are the model's own values, not "
    "probabilities. "
    "Frames between the sampled ones were not looked at."
)


def _video(session: Session, project_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
    if session.get(Project, project_id) is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    asset = session.get(Asset, asset_id)
    if asset is None or asset.project_id != project_id:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found in this project")
    if asset.modality != "video":
        raise ProblemError(409, "NOT_A_VIDEO", "This asset is not a video")
    return asset


def _run(session: Session, asset: Asset, run_id: uuid.UUID) -> PipelineRun:
    run = session.get(PipelineRun, run_id)
    if run is None or run.asset_id != asset.id or run.pipeline != PIPELINE:
        raise ProblemError(404, "RUN_NOT_FOUND", "No video analysis run with this id for this video")
    return run


@router.get("/plan", response_model=VideoSamplingPlan)
def sampling_plan(
    project_id: uuid.UUID,
    asset_id: uuid.UUID,
    session: SessionDep,
    interval_s: Annotated[float, Query(gt=0, le=3600, allow_inf_nan=False)],
    max_frames: Annotated[int, Query(ge=1, le=300)],
    start_s: Annotated[float, Query(ge=0, allow_inf_nan=False)] = 0.0,
    end_s: Annotated[float | None, Query(gt=0, allow_inf_nan=False)] = None,
):
    """How many frames a sampling choice would take, before anything is run. The job re-derives the exact frames."""
    asset = _video(session, project_id, asset_id)
    settings = get_settings()
    policy = SamplingPolicy(interval_s=interval_s, max_frames=max_frames, start_s=start_s, end_s=end_s)
    warnings: list[str] = []
    try:
        policy.validate(
            min_interval_s=settings.video_analysis_min_interval_s, frames_ceiling=settings.video_analysis_max_frames
        )
        duration_ms = asset.video.duration_ms if asset.video else None
        planned: int | None
        wanted: list[int] = []
        if duration_ms is not None:
            # The last frame is shown one frame-length before the end of the stated duration; the job uses the real
            # frame times, so this is an estimate and says so.
            rate = asset.video.frame_rate if asset.video else None
            last_time_s = max(0.0, duration_ms / 1000 - (1 / rate if rate else 0.0))
            times = wanted_times(policy, last_time_s)
            wanted, planned = [round(t * 1000) for t in times], len(times)
            basis = (
                "Worked out from the container's stated duration. The analysis reads the video's real frame times "
                "and may "
                "take slightly fewer frames (a frame hit by two wanted times is analysed once)."
            )
        elif end_s is not None:
            times = wanted_times(policy, end_s)
            wanted, planned = [round(t * 1000) for t in times], len(times)
            basis = "Worked out from the end time you gave; the container states no duration."
            warnings.append("The container states no duration, so the end of the video is not known.")
        else:
            planned, basis = (
                None,
                "The container states no duration and no end time was given, so the count is not known yet.",
            )
            warnings.append("Give an end time to see how many frames this would take.")
    except SamplingError as exc:
        raise ProblemError(422, exc.code, "This sampling is not allowed", str(exc)) from exc
    if planned is not None and planned * policy.interval_s < (duration_ms or 0) / 1000 * 0.2:
        warnings.append("This covers only a small part of the video.")
    return VideoSamplingPlan(
        sampling=VideoSampling(interval_s=interval_s, max_frames=max_frames, start_s=start_s, end_s=end_s),
        planned_frames=planned,
        wanted_ms=wanted,
        duration_ms=duration_ms,
        basis=basis,
        min_interval_s=settings.video_analysis_min_interval_s,
        max_frames_ceiling=settings.video_analysis_max_frames,
        policy=POLICY_TEXT,
        warnings=warnings,
    )


def _frame_artifacts(session: Session, asset: Asset, run_id: uuid.UUID) -> list[Artifact]:
    rows = session.scalars(
        select(Artifact).where(
            Artifact.asset_id == asset.id,
            Artifact.kind == FRAME_ARTIFACT_KIND,
            Artifact.details["run_id"].astext == str(run_id),
        )
    ).all()
    return sorted(rows, key=lambda a: a.details["frame_index"])


@router.get("/runs/{run_id}/frames", response_model=VideoFramesOut)
def run_frames(project_id: uuid.UUID, asset_id: uuid.UUID, run_id: uuid.UUID, session: SessionDep):
    """The sampled frames of one analysis run with their frame numbers, times, checksums and how many findings each "
    "has."""
    asset = _video(session, project_id, asset_id)
    run = _run(session, asset, run_id)
    frame_key = ObservationRecord.record["evidence"][0]["frame_index"].astext
    counts = {
        int(key): int(total)
        for key, total in session.execute(
            select(frame_key, func.count()).where(ObservationRecord.run_id == run.id).group_by(frame_key)
        )
        if key is not None
    }
    base = f"/api/v1/projects/{project_id}/assets/{asset.id}/video-analysis/runs/{run.id}/frames"
    frames = [
        VideoFrameOut(
            frame_index=a.details["frame_index"],
            timestamp_ms=a.details["timestamp_ms"],
            sample_target_ms=a.details["sample_target_ms"],
            also_stands_for_ms=a.details.get("also_stands_for_ms", []),
            width=a.width,
            height=a.height,
            sha256=a.sha256,
            size_bytes=a.size_bytes,
            finding_count=counts.get(a.details["frame_index"], 0),
            image_url=f"{base}/{a.details['frame_index']}/image",
        )
        for a in _frame_artifacts(session, asset, run.id)
    ]
    summary = run.summary or {}
    sampling = (run.settings or {}).get("sampling") or {}
    return VideoFramesOut(
        run_id=run.id,
        asset_id=asset.id,
        source_sha256=asset.blob_sha256,
        model_name=run.model_name,
        model_version=run.model_version,
        sampling=VideoSampling(**sampling),
        frames=frames,
        failed=[VideoFrameFailureOut(**item) for item in summary.get("frames_failed", [])],
        findings_total=sum(counts.values()),
        frames_in_file=summary.get("video_frames_in_file"),
        policy=POLICY_TEXT,
        frame_numbering=FRAME_NUMBERING,
        coordinates=COORDINATES,
        note=NOTE,
    )


@router.get(
    "/runs/{run_id}/frames/{frame_index}/image",
    response_class=FileResponse,
    responses={
        200: {"description": "The stored frame image (JPEG) that was analysed", "content": {"image/jpeg": {}}},
        **problem_responses(404, 409, 500),
    },
)
def frame_image(project_id: uuid.UUID, asset_id: uuid.UUID, run_id: uuid.UUID, frame_index: int, session: SessionDep):
    """The exact frame image the model saw, as stored. Its SHA-256 is the ETag and is recorded on every finding."""
    asset = _video(session, project_id, asset_id)
    run = _run(session, asset, run_id)
    artifact = next(
        (a for a in _frame_artifacts(session, asset, run.id) if a.details["frame_index"] == frame_index), None
    )
    if artifact is None:
        raise ProblemError(404, "FRAME_NOT_FOUND", "This run has no stored frame with that number")
    path = store.absolute_path(artifact.storage_path)
    if not path.is_file():
        raise ProblemError(500, "STORAGE_ERROR", "The frame file is missing", "Run the analysis again to recreate it.")
    return FileResponse(path, media_type=artifact.media_type, headers={"ETag": f'"{artifact.sha256}"'})
