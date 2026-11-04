"""Video-frame pipeline: the existing image detector, run on a bounded sample of frames of one video.

It reports where in a sampled frame the model sees something and what class the model assigns. It does not track: a
detection in one frame says nothing about any other frame, and nothing here claims two detections are the same
object. It
does not say where on the Earth anything is; a video has no georeferencing and none is inferred.

Reuse, not a second detector: the model, its adapter, the box scaling, the category naming, the observation contract and
the review states are those of the ground-image pipeline. Only the source of the pixels (an extracted frame) and the
evidence (frame number, time, and the frame's checksum) are new.

Everything the job produces is written in one transaction at the end, so a cancelled or failed job leaves no findings;
the frame files it wrote are removed with it.
"""

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from geopoc.config import get_settings
from geopoc.contracts.observation import Observation
from geopoc.contracts.review import initial_status
from geopoc.db.models import Artifact, Asset, Blob, ObservationRecord, PipelineRun, utcnow
from geopoc.jobs.handlers import JobContext, JobError, Outcome, TransientJobError
from geopoc.pipelines.base import Detection, load_adapter
from geopoc.pipelines.ground_image import (
    DEFAULT_MAX_INPUT_SIDE,
    _sha256,
    category_id,
    load_for_inference,
    to_source_pixels,
)
from geopoc.registry.manifest import ModelEntry, load_manifest, weights_problem
from geopoc.storage import store
from geopoc.video import media
from geopoc.video.sampling import (
    FRAME_MODEL_PIPELINES,
    POLICY_TEXT,
    SamplingError,
    SamplingPolicy,
    SelectedFrame,
    select_frames,
)

PIPELINE = "video"
FRAME_ARTIFACT_KIND = "video_frame"
BYTES_PER_PIXEL_ESTIMATE = 1.0  # a generous upper bound for a high-quality JPEG, used only to refuse a full disk early

FRAME_NUMBERING = (
    "Frame number: 0-based position of the frame in presentation order, counted over every frame of the first video "
    "stream. Timestamp: the frame's presentation time in milliseconds from the first frame (0 = first frame), the "
    "clock a "
    "player shows. Both come from the container's own frame times; none is estimated from a frame rate."
)
COORDINATES = (
    "Box coordinates are in the pixel grid of the extracted frame image (top-left origin), after any rotation the "
    "container asks for, scaled back from the reduced image the model saw. The frame image is stored as evidence and "
    "its "
    "SHA-256 is recorded on every finding."
)


def frame_path(asset_id: uuid.UUID, run_id: uuid.UUID, frame_index: int) -> str:
    return f"video_frames/{asset_id}/{run_id}/f{frame_index:08d}.jpg"


def frame_variant(run_id: uuid.UUID, frame_index: int) -> str:
    return f"{run_id.hex}-{frame_index:08d}"


@dataclass
class FrameResult:
    selected: SelectedFrame
    width: int
    height: int
    sha256: str
    size_bytes: int
    relative_path: str
    container_pts_s: float
    observations: list[Observation]


def build_frame_observation(
    *,
    detection: Detection,
    entry: ModelEntry,
    asset: Asset,
    run_id: uuid.UUID,
    frame: SelectedFrame,
    frame_sha256: str,
    inferred_size: tuple[int, int],
    frame_size: tuple[int, int],
    container_pts_s: float,
) -> dict[str, Any]:
    flags = ["NOT_GEOREFERENCED"]
    if not entry.score_calibrated:
        flags.append("UNCALIBRATED_SCORE")
    status = initial_status(quality_flags=flags, score_calibrated=entry.score_calibrated)
    seconds = frame.timestamp_ms / 1000
    return {
        "observation_id": uuid.uuid4(),
        "project_id": asset.project_id,
        "asset_id": asset.id,
        "pipeline": PIPELINE,
        "model": {"name": entry.name, "version": entry.version, "run_id": run_id},
        "claim": {
            "category": category_id(entry, detection.label),
            "description": (
                f"Model-predicted '{detection.label}'-like feature in the marked region of frame {frame.frame_index} "
                f"({seconds:.3f} s into the video). Geographic location not established. Not tracked: this does not "
                "say it is the same object as anything in another frame."
            ),
            "claim_type": "inferred",
        },
        "score": {
            "raw_value": detection.score,
            "semantics": entry.score_semantics,
            "calibrated": entry.score_calibrated,
            "value_range": entry.score_range,
        },
        "evidence": [
            {
                "asset_id": asset.id,
                "region_px": {
                    "type": "bbox_xyxy",
                    "coordinates": to_source_pixels(detection.box, inferred_size, frame_size),
                    "coordinate_space": "source_image_pixels",
                    "origin": "top_left",
                    "pixel_reference": "corner",
                    "image_width": frame_size[0],
                    "image_height": frame_size[1],
                },
                "frame_index": frame.frame_index,
                "timestamp_ms": frame.timestamp_ms,
                # Binds the finding to the exact frame image that was analysed.
                "source_excerpt_or_metadata_key": f"video_frame_sha256:{frame_sha256}",
            }
        ],
        "geospatial": {"geometry": None},
        "measurements": [],
        "quality_flags": flags,
        "review": {"status": status},
        "provenance": {
            "source_checksum": asset.blob_sha256,
            "ingested_at": asset.ingested_at,
            "transformations": [
                {
                    "type": "frame_extraction",
                    "tool": "ffmpeg",
                    "frame_index": frame.frame_index,
                    "timestamp_ms": frame.timestamp_ms,
                    "container_pts_s": container_pts_s,
                    "output": "image/jpeg",
                    "output_sha256": frame_sha256,
                }
            ],
            "created_at": utcnow(),
        },
    }


def _policy_from(payload: dict[str, Any]) -> SamplingPolicy:
    raw = payload.get("sampling") or {}
    settings = get_settings()
    try:
        return SamplingPolicy(
            interval_s=raw["interval_s"],
            max_frames=int(raw["max_frames"]),
            start_s=raw.get("start_s", 0.0),
            end_s=raw.get("end_s"),
        ).validate(
            min_interval_s=settings.video_analysis_min_interval_s, frames_ceiling=settings.video_analysis_max_frames
        )
    except KeyError as exc:
        raise JobError("SAMPLING_INVALID", f"The job has no {exc.args[0]} in its sampling settings.") from exc
    except SamplingError as exc:
        raise JobError(exc.code, str(exc)) from exc


def _remove_run_files(run_dir: Path) -> None:
    shutil.rmtree(run_dir, ignore_errors=True)  # only files this job wrote, under its own run folder


def run_video_analysis_job(ctx: JobContext) -> Outcome:
    session, job = ctx.session, ctx.job
    payload = job.payload
    settings = get_settings()
    started = utcnow()

    # --- validating: everything that can be known to be wrong before any frame is read ---------------------------
    ctx.stage("checking inputs")
    asset = session.get(Asset, job.asset_id) if job.asset_id else None
    if asset is None:
        raise JobError("ASSET_NOT_FOUND", "The asset this job refers to does not exist.")
    if payload.get("pipeline") != PIPELINE or asset.modality != "video":
        raise JobError("PIPELINE_INCOMPATIBLE", "The video pipeline only accepts videos.")
    model = payload.get("model") or {}
    entry = load_manifest().get(str(model.get("name")), str(model.get("version")))
    if entry is None or entry.pipeline not in FRAME_MODEL_PIPELINES:
        raise JobError("MODEL_UNKNOWN", "The requested model is not in the model manifest, or cannot analyse frames.")
    problem = weights_problem(entry, verify_checksum=True)
    if problem:
        raise JobError("MODEL_UNAVAILABLE", problem)
    policy = _policy_from(payload)

    blob = session.get(Blob, asset.blob_sha256)
    original = store.absolute_path(blob.storage_path)
    if not original.is_file():
        raise JobError("STORAGE_ERROR", "The stored original is missing.")
    if _sha256(original) != asset.blob_sha256:
        raise JobError("STORAGE_ERROR", "The stored original no longer matches its checksum; it is not analysed.")

    # --- choosing the frames: from the container's own frame times, before any model is loaded --------------------
    ctx.stage("reading frame times", progress=0.05, status="processing")
    try:
        scan = media.scan_timeline(original, settings)
        frames = select_frames(policy, scan.timeline)
    except media.MediaToolingUnavailable as exc:
        raise JobError(
            "MEDIA_TOOLING_UNAVAILABLE", f"{exc} Video frames cannot be taken without ffmpeg and ffprobe."
        ) from exc
    except media.FrameExtractionError as exc:
        raise JobError(exc.code, str(exc)) from exc
    except SamplingError as exc:
        raise JobError(exc.code, str(exc)) from exc
    wanted = len(frames) + sum(len(f.also_stands_for_s) for f in frames)
    ctx.event(
        f"{len(frames)} distinct frame(s) chosen for {wanted} wanted time(s) "
        f"(interval {policy.interval_s:g} s, limit {policy.max_frames})."
    )

    width, height = (asset.video.width, asset.video.height) if asset.video else (1920, 1080)
    needed = int(len(frames) * width * height * BYTES_PER_PIXEL_ESTIMATE)
    if store.free_bytes() < settings.storage_reserve_bytes + needed:
        raise JobError(
            "INSUFFICIENT_STORAGE", "There is not enough free disk space to keep the frames for this analysis."
        )

    # --- processing ------------------------------------------------------------------------------------------------
    ctx.stage("loading model", progress=0.1)
    adapter = load_adapter(entry)
    try:
        load_info = adapter.load(entry)
    except ImportError as exc:
        raise JobError("MODEL_RUNTIME_UNAVAILABLE", f"The model's runtime is not installed: {exc}") from exc
    except Exception as exc:
        raise JobError("MODEL_LOAD_FAILED", f"The model could not be loaded: {type(exc).__name__}: {exc}") from exc

    run_id = uuid.uuid4()
    run_dir = store.absolute_path(f"video_frames/{asset.id}/{run_id}")
    run_dir.mkdir(parents=True, exist_ok=False)
    ctx.discard.append(lambda: _remove_run_files(run_dir))  # also covers a stop after this function has returned
    max_side = int(entry.settings.get("max_input_side", DEFAULT_MAX_INPUT_SIDE))
    done: list[FrameResult] = []
    failed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    below_min_score = 0
    try:
        for position, frame in enumerate(frames):
            ctx.progress(0.12 + 0.8 * position / len(frames))  # also where a cancellation takes effect
            relative = frame_path(asset.id, run_id, frame.frame_index)
            destination = store.absolute_path(relative)
            try:
                frame_w, frame_h, delivered = media.extract_frame(
                    original, frame.absolute_s, scan.seek_origin_s, destination, settings
                )
                picture, frame_size = load_for_inference(destination, max_side)
            except media.MediaToolingUnavailable as exc:
                raise JobError(
                    "MEDIA_TOOLING_UNAVAILABLE", f"{exc} Video frames cannot be taken without ffmpeg."
                ) from exc
            except media.FrameExtractionError as exc:
                failed.append(_failure(frame, exc.code, str(exc)))
                continue
            except (OSError, ValueError) as exc:
                destination.unlink(missing_ok=True)
                failed.append(
                    _failure(
                        frame, "FRAME_UNREADABLE", f"The extracted frame could not be decoded: {type(exc).__name__}"
                    )
                )
                continue
            checksum = _sha256(destination)
            try:
                inference = adapter.infer(picture)
            except Exception as exc:
                if "out of memory" in str(exc).lower():
                    raise TransientJobError("GPU_OUT_OF_MEMORY", "The GPU ran out of memory during inference.") from exc
                raise JobError(
                    "INFERENCE_FAILED", f"The model failed on frame {frame.frame_index}: {type(exc).__name__}: {exc}"
                ) from exc
            below_min_score += inference.below_min_score
            accepted: list[Observation] = []
            for detection in inference.detections:
                candidate = build_frame_observation(
                    detection=detection, entry=entry, asset=asset, run_id=run_id, frame=frame, frame_sha256=checksum,
                    inferred_size=picture.size, frame_size=frame_size, container_pts_s=delivered,
                )  # fmt: skip
                try:
                    accepted.append(Observation.model_validate(candidate))
                except ValidationError as exc:
                    rejected.append(
                        {
                            "frame_index": frame.frame_index,
                            "label": detection.label,
                            "score": detection.score,
                            "errors": [error["msg"] for error in exc.errors()][:5],
                        }
                    )
            done.append(
                FrameResult(
                    frame, frame_w, frame_h, checksum, destination.stat().st_size, relative, delivered, accepted
                )
            )

        if not done:
            raise JobError(
                "FRAME_EXTRACTION_FAILED", "No frame could be taken from this video. " + _first_reason(failed)
            )

        # --- one transaction: the run, its frames and its findings, or nothing ---------------------------------------
        ctx.stage("saving results", progress=0.95)
        ctx.check_cancel()
        observation_total = sum(len(item.observations) for item in done)
        settings_used = {
            **entry.settings,
            "max_input_side": max_side,
            "sampling": policy.as_dict(),
            "sampling_policy": POLICY_TEXT,
            "frame_numbering": FRAME_NUMBERING,
            "coordinates": COORDINATES,
            "frame_jpeg": "ffmpeg mjpeg, -q:v 2; the analysed pixels are the stored JPEG's",
            "note": "min_score is an operational cutoff from the manifest, not a calibrated threshold.",
        }
        summary: dict[str, Any] = {
            "observations": observation_total,
            "below_min_score": below_min_score,
            "rejected_outputs": rejected,
            "frames_planned": len(frames),
            "frames_wanted_times": wanted,
            "frames_analysed": len(done),
            "frames_failed": failed,
            "video_frames_in_file": scan.timeline.frame_count,
            "video_first_pts_s": scan.timeline.first_pts_s,
            "video_last_time_s": scan.timeline.last_time_s,
            **{k: v for k, v in load_info.items() if k != "libraries"},
        }
        session.add(
            PipelineRun(
                id=run_id,
                job_id=job.id,
                project_id=asset.project_id,
                asset_id=asset.id,
                pipeline=PIPELINE,
                model_name=entry.name,
                model_version=entry.version,
                weights_sha256=entry.weights_sha256,
                settings=settings_used,
                device=load_info.get("device"),
                library_versions={**load_info.get("libraries", {}), "ffmpeg": media.tool_version("ffmpeg")},
                summary=summary,
                started_at=started,
                finished_at=utcnow(),
            )
        )
        session.flush()
        for item in done:
            session.add(
                Artifact(
                    asset_id=asset.id,
                    kind=FRAME_ARTIFACT_KIND,
                    variant=frame_variant(run_id, item.selected.frame_index),
                    storage_path=item.relative_path,
                    media_type="image/jpeg",
                    width=item.width,
                    height=item.height,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    details={
                        "run_id": str(run_id),
                        "frame_index": item.selected.frame_index,
                        "timestamp_ms": item.selected.timestamp_ms,
                        "relative_time_s": item.selected.relative_s,
                        "container_pts_s": item.container_pts_s,
                        "sample_target_ms": round(item.selected.target_s * 1000),
                        "also_stands_for_ms": [round(t * 1000) for t in item.selected.also_stands_for_s],
                        "source_video_sha256": asset.blob_sha256,
                    },
                    created_by_job_id=job.id,
                )
            )
            for observation in item.observations:
                session.add(
                    ObservationRecord(
                        id=observation.observation_id,
                        project_id=asset.project_id,
                        asset_id=asset.id,
                        run_id=run_id,
                        pipeline=PIPELINE,
                        category=observation.claim.category,
                        current_category=observation.claim.category,
                        review_status=observation.review.status.value,
                        score_raw=observation.score.raw_value,
                        quality_flags=observation.quality_flags,
                        record=observation.model_dump(mode="json"),
                        created_at=observation.provenance.created_at,
                    )
                )
        session.flush()
    except BaseException:
        _remove_run_files(run_dir)  # cancelled, failed or refused: no frame files without a run to belong to
        raise

    result = {
        "run_id": str(run_id),
        "observations": observation_total,
        "below_min_score": below_min_score,
        "rejected_outputs": len(rejected),
        "frames_planned": len(frames),
        "frames_analysed": len(done),
        "frames_failed": len(failed),
    }
    if failed or rejected:
        parts = []
        if failed:
            parts.append(f"{len(failed)} of {len(frames)} frame(s) could not be taken")
        if rejected:
            parts.append(f"{len(rejected)} model output(s) failed validation")
        return Outcome("completed_with_warnings", result, "; ".join(parts) + ".")
    if any(o.review.status.value != "proposed" for item in done for o in item.observations):
        return Outcome("needs_review", result, f"{observation_total} finding(s) in {len(done)} frame(s) need review.")
    if not observation_total:
        return Outcome(
            "completed",
            result,
            f"The model reported no findings above its cutoff in {len(done)} sampled frame(s). That is a valid result.",
        )
    return Outcome("completed", result)


def _failure(frame: SelectedFrame, code: str, message: str) -> dict[str, Any]:
    return {"frame_index": frame.frame_index, "timestamp_ms": frame.timestamp_ms, "code": code, "message": message}


def _first_reason(failed: list[dict[str, Any]]) -> str:
    return f"First failure: {failed[0]['code']}: {failed[0]['message']}" if failed else ""
