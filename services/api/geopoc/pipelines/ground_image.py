"""Ground-image pipeline: general-purpose object detection in a ground photograph.

It reports where in the image the model sees something and what class the model assigns. It does not say where
on the Earth anything is: a ground photograph has no georeferencing, and none is inferred. It does not import
or depend on the overhead or video pipelines.
"""

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError

from geopoc.contracts.observation import Observation
from geopoc.contracts.review import initial_status
from geopoc.db.models import Asset, Blob, ObservationRecord, PipelineRun, utcnow
from geopoc.jobs.handlers import JobContext, JobError, Outcome, TransientJobError
from geopoc.pipelines.base import Detection, load_adapter
from geopoc.registry.manifest import ModelEntry, load_manifest, weights_problem
from geopoc.review.linking import link_run
from geopoc.storage import store

PIPELINE = "ground_image"
# The image is reduced to this longest side before inference, which bounds memory whatever the photo's size.
DEFAULT_MAX_INPUT_SIDE = 1333


def category_id(entry: ModelEntry, label: str) -> str:
    """A taxonomy id built from the model's own class list, e.g. 'coco:traffic light'."""
    namespace = str(entry.settings.get("taxonomy", entry.name)).lower()
    return f"{re.sub(r'[^a-z0-9_]', '_', namespace)}:{re.sub(r'[^a-z0-9_ .-]', '_', label.lower())}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_for_inference(path: Path, max_side: int) -> tuple[Image.Image, tuple[int, int]]:
    """Decode the stored original as RGB, reduced to at most `max_side`. Returns it with the original size.

    Pixels are taken as stored: EXIF orientation is not applied, matching the contract's pixel convention.
    """
    with Image.open(path) as image:
        source = image.size
        if image.format == "JPEG":
            image.draft("RGB", (max_side, max_side))
        picture = image.convert("RGB")
    picture.thumbnail((max_side, max_side))
    return picture, source


def to_source_pixels(box: tuple[float, ...], inferred: tuple[int, int], source: tuple[int, int]) -> list[float]:
    """Scale a box from the image the model saw back to the original's pixel grid, clamped to the image."""
    scale_x, scale_y = source[0] / inferred[0], source[1] / inferred[1]
    x0, y0, x1, y1 = box
    return [
        round(min(max(x0 * scale_x, 0.0), source[0]), 2),
        round(min(max(y0 * scale_y, 0.0), source[1]), 2),
        round(min(max(x1 * scale_x, 0.0), source[0]), 2),
        round(min(max(y1 * scale_y, 0.0), source[1]), 2),
    ]


def build_observation(
    *,
    detection: Detection,
    entry: ModelEntry,
    asset: Asset,
    run_id: uuid.UUID,
    inferred_size: tuple[int, int],
    source_size: tuple[int, int],
) -> dict[str, Any]:
    flags = ["NOT_GEOREFERENCED"]
    if not entry.score_calibrated:
        flags.append("UNCALIBRATED_SCORE")
    status = initial_status(quality_flags=flags, score_calibrated=entry.score_calibrated)
    now = utcnow()
    return {
        "observation_id": uuid.uuid4(),
        "project_id": asset.project_id,
        "asset_id": asset.id,
        "pipeline": PIPELINE,
        "model": {"name": entry.name, "version": entry.version, "run_id": run_id},
        "claim": {
            "category": category_id(entry, detection.label),
            # What the model output is, and nothing about identity, purpose or location.
            "description": (
                f"Model-predicted '{detection.label}'-like feature in the marked image region. "
                "Geographic location not established."
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
                    "coordinates": to_source_pixels(detection.box, inferred_size, source_size),
                    "coordinate_space": "source_image_pixels",
                    "origin": "top_left",
                    "pixel_reference": "corner",
                    "image_width": source_size[0],
                    "image_height": source_size[1],
                },
            }
        ],
        "geospatial": {"geometry": None},
        "measurements": [],
        "quality_flags": flags,
        "review": {"status": status},
        "provenance": {
            "source_checksum": asset.blob_sha256,
            "ingested_at": asset.ingested_at,
            "transformations": [],
            "created_at": now,
        },
    }


def run_analysis_job(ctx: JobContext) -> Outcome:
    session, job = ctx.session, ctx.job
    payload = job.payload
    started = utcnow()

    # --- validating: everything that can be known to be wrong before any model is loaded ---------------------
    ctx.stage("checking inputs")
    asset = session.get(Asset, job.asset_id) if job.asset_id else None
    if asset is None:
        raise JobError("ASSET_NOT_FOUND", "The asset this job refers to does not exist.")
    if payload.get("pipeline") != PIPELINE or asset.modality != PIPELINE:
        raise JobError("PIPELINE_INCOMPATIBLE", "The ground-image pipeline only accepts ground photographs.")
    model = payload.get("model") or {}
    entry = load_manifest().get(str(model.get("name")), str(model.get("version")))
    if entry is None or entry.pipeline != PIPELINE:
        raise JobError("MODEL_UNKNOWN", "The requested model is not in the model manifest for this pipeline.")
    problem = weights_problem(entry, verify_checksum=True)
    if problem:
        raise JobError("MODEL_UNAVAILABLE", problem)

    blob = session.get(Blob, asset.blob_sha256)
    original = store.absolute_path(blob.storage_path)
    if not original.is_file():
        raise JobError("STORAGE_ERROR", "The stored original is missing.")
    if _sha256(original) != asset.blob_sha256:
        raise JobError("STORAGE_ERROR", "The stored original no longer matches its checksum; it is not analysed.")

    # --- processing ------------------------------------------------------------------------------------------
    ctx.stage("loading model", progress=0.1, status="processing")
    adapter = load_adapter(entry)
    try:
        load_info = adapter.load(entry)
    except ImportError as exc:
        raise JobError("MODEL_RUNTIME_UNAVAILABLE", f"The model's runtime is not installed: {exc}") from exc
    except Exception as exc:
        raise JobError("MODEL_LOAD_FAILED", f"The model could not be loaded: {type(exc).__name__}: {exc}") from exc

    ctx.stage("reading image", progress=0.4)
    max_side = int(entry.settings.get("max_input_side", DEFAULT_MAX_INPUT_SIDE))
    try:
        picture, source_size = load_for_inference(original, max_side)
    except (OSError, ValueError) as exc:
        raise JobError("ASSET_UNREADABLE", "The stored image could not be decoded.") from exc

    ctx.stage("running model", progress=0.5)
    try:
        inference = adapter.infer(picture)
    except Exception as exc:
        if "out of memory" in str(exc).lower():
            raise TransientJobError("GPU_OUT_OF_MEMORY", "The GPU ran out of memory during inference.") from exc
        raise JobError("INFERENCE_FAILED", f"The model failed on this image: {type(exc).__name__}: {exc}") from exc

    # --- validate every output against the contract; nothing invalid is stored, nothing is dropped silently ----
    ctx.stage("validating outputs", progress=0.8)
    run_id = uuid.uuid4()
    accepted: list[Observation] = []
    rejected: list[dict[str, Any]] = []
    for detection in inference.detections:
        candidate = build_observation(
            detection=detection,
            entry=entry,
            asset=asset,
            run_id=run_id,
            inferred_size=picture.size,
            source_size=source_size,
        )
        try:
            accepted.append(Observation.model_validate(candidate))
        except ValidationError as exc:
            rejected.append(
                {
                    "label": detection.label,
                    "score": detection.score,
                    "box": list(detection.box),
                    "errors": [error["msg"] for error in exc.errors()][:5],
                }
            )

    settings_used = {
        **entry.settings,
        "max_input_side": max_side,
        "inference_size": list(picture.size),
        "source_size": list(source_size),
        "note": "min_score is an operational cutoff from the manifest, not a calibrated threshold.",
    }
    summary = {
        "observations": len(accepted),
        "below_min_score": inference.below_min_score,
        "rejected_outputs": rejected,
        **inference.info,
        **{k: v for k, v in load_info.items() if k != "libraries"},
    }
    ctx.check_cancel()
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
            library_versions=load_info.get("libraries", {}),
            summary=summary,
            started_at=started,
            finished_at=utcnow(),
        )
    )
    session.flush()
    for observation in accepted:
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
    # Deterministic links and conflicts with earlier runs on the same image content (Phase 4).
    linked = link_run(session, run_id)

    result = {"run_id": str(run_id), **{k: summary[k] for k in ("observations", "below_min_score")}}
    result["rejected_outputs"] = len(rejected)
    result.update(linked)
    if rejected:
        return Outcome("completed_with_warnings", result, f"{len(rejected)} model output(s) failed validation.")
    if any(o.review.status.value != "proposed" for o in accepted):
        return Outcome("needs_review", result, f"{len(accepted)} observation(s) need review.")
    if not accepted:
        return Outcome("completed", result, "The model reported no findings above its cutoff. That is a valid result.")
    return Outcome("completed", result)
