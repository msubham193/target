"""Background jobs for the non-AI raster tools (Phase 3b): band statistics, display copies, pixel comparisons.

None of these is AI analysis and none produces an observation. Originals are only read; everything written is a
derived file under DATA_ROOT/derived (statistics, display copies) or a new derived asset with lineage (a pixel
comparison).
"""

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import rasterio
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from geopoc.config import get_settings
from geopoc.db.models import Artifact, Asset, AssetLineage, Blob, Project, WorkspaceUser, utcnow
from geopoc.geo import compare, displaycopy, georeference, rasterstats
from geopoc.jobs.handlers import JobContext, JobError, Outcome, TransientJobError
from geopoc.storage import store

STATISTICS_KIND, STATISTICS_VARIANT = "statistics", "bands"
DISPLAY_KIND, DISPLAY_VARIANT = "display_copy", "cog"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _raster_asset(session: Session, asset_id: uuid.UUID | None) -> tuple[Asset, Path]:
    asset = session.get(Asset, asset_id) if asset_id else None
    if asset is None or asset.modality != "overhead_raster" or asset.raster is None:
        raise JobError("NOT_A_RASTER", "This job needs a raster asset.")
    blob = session.get(Blob, asset.blob_sha256)
    original = store.absolute_path(blob.storage_path) if blob else None
    if original is None or not original.is_file():
        raise JobError("STORAGE_ERROR", "The stored original is missing.")
    return asset, original


def artifact(session: Session, asset_id: uuid.UUID, kind: str) -> Artifact | None:
    return session.scalars(select(Artifact).where(Artifact.asset_id == asset_id, Artifact.kind == kind)).first()


def statistics(session: Session, asset_id: uuid.UUID) -> dict[str, Any] | None:
    found = artifact(session, asset_id, STATISTICS_KIND)
    return found.details if found else None


def display_source(session: Session, asset: Asset, original: Path) -> tuple[Path, str]:
    """The file to read for drawing: the verified display copy if one exists, otherwise the original."""
    copy = artifact(session, asset.id, DISPLAY_KIND)
    if copy is not None and (copy.details or {}).get("verification"):
        path = store.absolute_path(copy.storage_path)
        if path.is_file():
            return path, f"copy:{copy.sha256[:12]}"
    return original, "original"


def _replace_artifact(session: Session, asset: Asset, new: Artifact) -> None:
    session.execute(delete(Artifact).where(Artifact.asset_id == asset.id, Artifact.kind == new.kind))
    session.add(new)


# --- statistics -------------------------------------------------------------------------------------------------------


def run_statistics_job(ctx: JobContext) -> Outcome:
    session, job = ctx.session, ctx.job
    asset, original = _raster_asset(session, job.asset_id)
    ctx.stage("reading pixels", progress=0.0, status="processing")
    try:
        result = rasterstats.compute(original, get_settings().statistics_exact_max_pixels, progress=ctx.progress)
    except rasterstats.StatisticsUnsupported as exc:
        raise JobError("STATISTICS_UNSUPPORTED", str(exc)) from exc
    except (OSError, rasterio.errors.RasterioError) as exc:
        raise JobError("STATISTICS_FAILED", "The original could not be read to compute statistics.") from exc
    result["source_checksum"] = asset.blob_sha256
    ctx.stage("saving", progress=0.95)
    relative = f"derived/statistics/{asset.id}.json"
    target = store.absolute_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(result, indent=1).encode("utf-8")
    temp = target.with_suffix(".tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, target)
    except OSError as exc:
        raise TransientJobError("STORAGE_ERROR", "The statistics could not be written to local storage.") from exc
    ctx.check_cancel()
    _replace_artifact(
        session,
        asset,
        Artifact(
            asset_id=asset.id,
            kind=STATISTICS_KIND,
            variant=STATISTICS_VARIANT,
            storage_path=relative,
            media_type="application/json",
            width=result["width"],
            height=result["height"],
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            details=result,
            created_by_job_id=job.id,
        ),
    )
    return Outcome(
        result={
            "method": result["method"],
            "fraction_of_pixels": result["fraction_of_pixels"],
            "bands": result["band_count"],
        },
        message="Band statistics ready." if result["method"] == "exact" else "Band statistics ready (from a sample).",
    )


# --- display copy -----------------------------------------------------------------------------------------------------


def run_display_copy_job(ctx: JobContext) -> Outcome:
    session, job = ctx.session, ctx.job
    asset, original = _raster_asset(session, job.asset_id)
    ctx.stage("writing display copy", progress=None, status="processing")
    relative = f"derived/display/{asset.id}/display.tif"
    target = store.absolute_path(relative)
    try:
        result = displaycopy.build(original, target)
    except displaycopy.DisplayCopyError as exc:
        raise JobError("DISPLAY_COPY_MISMATCH", f"The display copy was not kept: {exc}") from exc
    except (OSError, rasterio.errors.RasterioError) as exc:
        raise JobError("DISPLAY_COPY_FAILED", f"The display copy could not be written: {exc}") from exc
    ctx.check_cancel()
    if _sha256(original) != asset.blob_sha256:
        target.unlink(missing_ok=True)
        raise JobError("ORIGINAL_CHANGED", "The original no longer matches its checksum; no copy is kept.")
    result["source_checksum"] = asset.blob_sha256
    _replace_artifact(
        session,
        asset,
        Artifact(
            asset_id=asset.id,
            kind=DISPLAY_KIND,
            variant=DISPLAY_VARIANT,
            storage_path=relative,
            media_type="image/tiff",
            width=asset.raster.width,
            height=asset.raster.height,
            size_bytes=result["size_bytes"],
            sha256=result["sha256"],
            details=result,
            created_by_job_id=job.id,
        ),
    )
    return Outcome(
        result={k: result[k] for k in ("size_bytes", "build_seconds")}
        | {"overviews": result["verification"]["overview_levels"]},
        message="Display copy verified against the original and ready.",
    )


# --- pixel comparison -------------------------------------------------------------------------------------------------


def run_compare_job(ctx: JobContext) -> Outcome:
    from geopoc.ingestion.service import ingest

    session, job = ctx.session, ctx.job
    payload = job.payload
    asset_a, path_a = _raster_asset(session, uuid.UUID(payload["asset_a"]))
    asset_b, path_b = _raster_asset(session, uuid.UUID(payload["asset_b"]))
    state_a, state_b = georeference.effective(asset_a.raster), georeference.effective(asset_b.raster)
    check = compare.check_alignment(
        asset_a.raster.width, asset_a.raster.height, state_a, asset_b.raster.width, asset_b.raster.height, state_b
    )
    if not check.aligned:
        raise JobError("NOT_ALIGNED", " ".join(check.reasons))
    for asset, path in ((asset_a, path_a), (asset_b, path_b)):
        if _sha256(path) != asset.blob_sha256:
            raise JobError(
                "ORIGINAL_CHANGED", f"The original of {asset.original_filename} no longer matches its checksum."
            )
    ctx.stage("computing B − A", progress=0.0, status="processing")
    temp = store.uploads_dir() / f"{uuid.uuid4().hex}.compare.tif"
    try:
        summary = compare.compute(
            path_a,
            int(payload["band_a"]),
            path_b,
            int(payload["band_b"]),
            temp,
            state_a.crs_wkt,
            state_a.geotransform,
            progress=lambda f: ctx.progress(0.9 * f),
        )
        ctx.stage("storing the derived raster", progress=0.92)
        sha = _sha256(temp)
        profile = session.get(WorkspaceUser, job.created_by)
        project = session.get(Project, asset_a.project_id)
        name = f"difference {Path(asset_b.original_filename).stem} minus {Path(asset_a.original_filename).stem}"
        name = (name[:180] if len(name) > 180 else name).replace("/", "_").replace("\\", "_") + ".tif"
        ingested = ingest(
            session,
            project=project,
            profile=profile,
            filename=name,
            modality="overhead_raster",
            family="tiff",
            temp_path=temp,
            sha256=sha,
            size_bytes=temp.stat().st_size,
            request_id=f"job:{job.id}",
        )
    finally:
        store.discard_temp(temp)
    derived = ingested.asset
    derived.origin = "derived"
    flags = list(dict.fromkeys([*derived.quality_flags, "DERIVED_ASSET"]))
    if "recorded" in (state_a.source, state_b.source):
        flags.append("CRS_USER_ASSERTED")  # placement rests on georeferencing recorded by a person
    derived.quality_flags = flags
    parameters = {
        "formula": "B - A",
        "band_a": int(payload["band_a"]),
        "band_b": int(payload["band_b"]),
        "alignment": "same CRS, geotransform and size; no resampling",
        "reason": payload.get("reason"),
        "summary": summary,
    }
    for role, asset, state in (("a", asset_a, state_a), ("b", asset_b, state_b)):
        session.add(
            AssetLineage(
                child_asset_id=derived.id,
                parent_asset_id=asset.id,
                relation="pixel_difference_of",
                role=role,
                tool=compare.TOOL,
                tool_version=compare.TOOL_VERSION,
                parameters=parameters,
                parent_checksum=asset.blob_sha256,
                parent_georef_version=state.version,
                created_by_job_id=job.id,
                created_at=utcnow(),
            )
        )
    return Outcome(
        result={
            "derived_asset_id": str(derived.id),
            "duplicate_of": str(ingested.duplicate_of_asset_id or "") or None,
            **summary,
        },
        message="Pixel difference stored as a derived raster.",
    )
