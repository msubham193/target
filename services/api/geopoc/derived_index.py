"""Background job `derived_index`: a spectral index (NDVI) from two locally stored Sentinel-2 L2A bands.

The bands are not application assets and are never copied or changed: they stay where the server's band folder keeps
them and are only read. What the job stores is one new derived GeoTIFF, registered through the ordinary ingestion path
as a raster asset (origin 'derived'), with the full provenance (both input files, their SHA-256, the per-band scaling,
the product, the formula, the range mask and what it removed) recorded on the asset and in the job's result.

Calculated values outside the index's analytical range ([-1, 1] for NDVI) are masked as nodata, never clamped.
"""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import rasterio

from geopoc.config import get_settings
from geopoc.db.models import Project, WorkspaceUser
from geopoc.geo import bandscan, indices
from geopoc.geo.indices import INDICES, BandInput, IndexInputError, IndexSpec, ProductInfo
from geopoc.jobs.handlers import JobContext, JobError, Outcome
from geopoc.storage import store

JOB_KIND = "derived_index"


def output_filename(product: ProductInfo, spec: IndexSpec) -> str:
    """e.g. T55HGS_20240115T235221_NDVI_10m.tif, from the product's own tile and sensing time."""
    stamp = re.sub(r"[^0-9T]", "", (product.start_time or "").split(".")[0])
    return f"{product.tile_id or 'tile'}_{stamp or 'unknown'}_{spec.key.upper()}_10m.tif"


def band_inputs(scene: bandscan.Scene) -> tuple[BandInput, BandInput]:
    """The scene's two bands in the index's order (first, second), with scaling taken from its product metadata."""
    if scene.product is None or not scene.compatible:
        raise IndexInputError("INPUTS_INCOMPATIBLE", " ".join(scene.problems) or "The bands cannot be combined.")
    first, second = scene.bands
    return (
        BandInput(first.path, first.band, indices.scaling_for(scene.product, first.band)),
        BandInput(second.path, second.band, indices.scaling_for(scene.product, second.band)),
    )


def produce(
    scene: bandscan.Scene, spec: IndexSpec, target: Path, *, progress=None, validate: bool = True
) -> dict[str, Any]:
    """Compute the masked index into `target`, reopen and validate it, and return everything worth recording.

    No database and no application storage is touched, so the real-data check and the job use exactly this code."""
    product = scene.product
    first, second = band_inputs(scene)
    summary = indices.compute_index(
        spec, product, first, second, target, progress=progress, valid_range=spec.analytical_range
    )
    report = indices.validate_output(target, first, second, summary) if validate else None
    if report is not None and report["failures"]:
        target.unlink(missing_ok=True)
        raise IndexInputError(
            "OUTPUT_VALIDATION_FAILED",
            "The calculated raster failed its own checks and was not kept: " + "; ".join(report["failures"]),
        )
    return {"summary": summary, "validation": report, "product": product, "first": first, "second": second}


def provenance(spec: IndexSpec, product: ProductInfo, made: dict[str, Any], scene_id: str) -> dict[str, Any]:
    """The record stored with the asset: what it was made from and how."""
    summary = made["summary"]
    inputs = []
    for role, band, key in ((spec.first_role, made["first"], "first"), (spec.second_role, made["second"], "second")):
        inputs.append(
            {
                "role": role,
                "band": band.band,
                "file_name": band.path.name,
                "sha256": summary["source_sha256"][key],
                "scaling": band.scaling.describe(),
            }
        )
    return {
        "index": spec.key,
        "title": spec.title,
        "formula": spec.formula,
        "tool": summary["tool"],
        "tool_version": summary["tool_version"],
        "gdal_version": summary["gdal_version"],
        "scene_id": scene_id,
        "range_mask": summary["range_mask"],
        "inputs": inputs,
        "product": {
            "uri": product.product_uri,
            "tile_id": product.tile_id,
            "acquisition_start": product.start_time,
            "processing_baseline": product.processing_baseline,
            "spacecraft": product.spacecraft,
            "metadata_sha256": product.metadata_sha256,
        },
        "counts": {
            "pixels": summary["pixels"],
            "valid_before_range_mask": summary["valid_before_range_mask"],
            "masked_out_of_range": summary["masked_out_of_range"],
            "valid": summary["valid_pixels"],
            "invalid": summary["invalid_pixels"],
            **summary["excluded"],
        },
    }


def _parse_time(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC) if text else None
    except ValueError:
        return None


def run_derived_index_job(ctx: JobContext) -> Outcome:
    from geopoc.ingestion.service import ingest

    session, job = ctx.session, ctx.job
    payload = job.payload
    spec = INDICES.get(payload.get("index", ""))
    if spec is None:
        raise JobError("INDEX_UNKNOWN", "This job names an index the server does not know.")
    ctx.stage("checking the inputs", progress=0.0, status="processing")
    scene = bandscan.find_scene(get_settings().imagery_band_root, spec, str(payload.get("scene_id", "")))
    if scene is None:
        raise JobError("SCENE_NOT_FOUND", "The scene is no longer in the server's band folder.")
    if not scene.compatible:
        raise JobError("INPUTS_INCOMPATIBLE", " ".join(scene.problems) or "The bands cannot be combined.")
    project = session.get(Project, job.project_id)
    profile = session.get(WorkspaceUser, job.created_by)
    if project is None or profile is None:
        raise JobError("PROJECT_NOT_FOUND", "The project or the person who started this job no longer exists.")

    temp = store.uploads_dir() / f"{uuid.uuid4().hex}.{spec.key}.tif"
    ctx.discard.append(lambda: temp.unlink(missing_ok=True))  # run if the attempt ends without its result being saved
    try:
        ctx.stage(f"calculating {spec.title}", progress=0.02)

        def calculating(fraction: float) -> None:  # fraction of the pixels done, reported as it happens
            ctx.progress(0.02 + 0.78 * fraction)

        try:
            made = produce(scene, spec, temp, progress=calculating, validate=False)
            ctx.stage("checking the result", progress=0.8)
            report = indices.validate_output(temp, made["first"], made["second"], made["summary"])
        except IndexInputError as exc:
            raise JobError(exc.code, str(exc)) from exc
        except (OSError, rasterio.errors.RasterioError) as exc:
            raise JobError("PROCESSING_FAILED", f"A band could not be read or the result written: {exc}") from exc
        made["validation"] = report
        if report["failures"]:
            raise JobError(
                "OUTPUT_VALIDATION_FAILED",
                "The calculated raster failed its own checks and was not kept: " + "; ".join(report["failures"]),
            )
        ctx.stage("storing the derived raster", progress=0.9)
        summary, product = made["summary"], made["product"]
        sha = indices.sha256_file(temp)
        size = temp.stat().st_size
        ingested = ingest(
            session,
            project=project,
            profile=profile,
            filename=output_filename(product, spec),
            modality="overhead_raster",
            family="tiff",
            temp_path=temp,
            sha256=sha,
            size_bytes=size,
            request_id=f"job:{job.id}",
        )
    finally:
        store.discard_temp(temp)

    derived = ingested.asset
    record = provenance(spec, product, made, scene.scene_id) | {"job_id": str(job.id)}
    derived.origin = "derived"
    derived.quality_flags = list(dict.fromkeys([*derived.quality_flags, "DERIVED_ASSET"]))
    derived.acquisition_time = derived.acquisition_time or _parse_time(product.start_time)
    derived.extracted_metadata = {**derived.extracted_metadata, "derived_index": record}
    job.asset_id = derived.id
    checks = report["checks"]
    result = {
        "derived_asset_id": str(derived.id),
        "duplicate_of_asset_id": str(ingested.duplicate_of_asset_id) if ingested.duplicate_of_asset_id else None,
        "index": spec.key,
        "scene_id": scene.scene_id,
        "file_name": derived.original_filename,
        "output_sha256": sha,
        "size_bytes": size,
        "width": summary["width"],
        "height": summary["height"],
        "crs": report["crs"],
        "resolution_m": abs(report["transform"][0]),
        "nodata": summary["nodata"],
        "formula": spec.formula,
        "range_mask": summary["range_mask"],
        "statistics": {
            "min": report["min"],
            "max": report["max"],
            "mean": report["mean"],
            "std": report["std"],
            "percentiles": report.get("percentiles", {}),
            "percentile_resolution": report.get("percentile_resolution"),
        },
        "counts": record["counts"] | {"valid_negative": summary["valid_negative"]},
        "inputs": record["inputs"],
        "product": record["product"],
        "validation": {
            "checks": len(checks),
            "passed": sum(1 for ok in checks.values() if ok),
            "failures": report["failures"],
            "sample_pixels_compared": report["sample_pixels_compared"],
            "sample_max_abs_difference": report["sample_max_abs_difference"],
            "strip_pixels_compared": report["strip_pixels_compared"],
            "strip_mismatches": report["strip_mismatches"],
        },
    }
    return Outcome(
        result=result,
        message=f"{spec.title} calculated and stored as a derived raster ({summary['masked_out_of_range']:,} "
        "out-of-range pixels masked).",
    )
