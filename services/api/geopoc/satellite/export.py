"""A small, bounded export into a dedicated scratch folder, with a provenance record written beside it.

The export is checked twice: before anything is requested (models.plan_export) and again on the file that came back. The
file is written exactly as the provider returned it and never altered; what is known about it goes into a separate
`.provenance.json` next to it, including the file's SHA-256, so the record can later be matched to the stored original.
Nothing here writes to the database, the project data folder or any existing file.
"""

import hashlib
import json
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import rasterio
from rasterio.errors import RasterioError

import geopoc
from geopoc.satellite.models import COLLECTION_ID, COLLECTION_TITLE, ExportPlan, SceneInfo
from geopoc.satellite.provider import SatelliteProvider

SCRATCH_PREFIX = "_satellite"
MANIFEST_SCHEMA = "geopoc.satellite.export/1"
GRID_TOLERANCE_PX = 2  # a provider may snap the grid by a pixel or so

LICENCE = {
    "data": "Copernicus Sentinel data, free and open under the Copernicus Sentinel Data Terms and Conditions.",
    "attribution": "Contains modified Copernicus Sentinel data {year}.",
    "service": "Distributed through Google Earth Engine, which has its own Terms of Service and quotas.",
    "status": "Summary for orientation, not legal advice: the account owner must confirm the terms that apply to "
    "this use.",
}
SYNTHETIC_LICENCE = {"data": "Generated locally for testing; no third-party licence applies.", "status": "synthetic"}
NOTES = [
    "This file is satellite imagery: measured digital numbers for a place and time, with the source recorded here.",
    "It is not a basemap tile. A basemap is a picture drawn for display and carries none of this provenance.",
    "Values are as delivered (unsigned 16-bit digital numbers). Reflectance is the value divided by 10000. No cloud "
    "masking, "
    "compositing, pan-sharpening or colour stretch has been applied.",
    "The cloud figure is the catalogue's share of cloudy pixels in the whole granule, not for this area.",
]


class ExportRefused(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ScratchDir:
    """A folder only this prototype writes to. Its name must start with `_satellite` (the scratch-folder convention)."""

    path: Path

    @classmethod
    def at(cls, path: Path | str) -> "ScratchDir":
        resolved = Path(path).resolve()
        if not resolved.name.startswith(SCRATCH_PREFIX):
            raise ExportRefused(
                "NOT_SCRATCH", f"The export folder's name must start with {SCRATCH_PREFIX!r}; got {resolved.name!r}"
            )
        if {"originals", "blobs", "previews"} & set(resolved.parts):
            raise ExportRefused("NOT_SCRATCH", "The export folder may not be inside the application's own file store")
        resolved.mkdir(parents=True, exist_ok=True)
        return cls(resolved)


@dataclass(frozen=True)
class ExportResult:
    tif_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stem_for(scene: SceneInfo, plan: ExportPlan) -> str:
    return f"s2sr_{scene.acquisition_time.astimezone(UTC):%Y%m%d}_{scene.tile or 'tile'}_{plan.aoi.fingerprint()}"


def _inspect(path: Path, plan: ExportPlan) -> dict[str, Any]:
    """Read the delivered file back and refuse it unless it is what was asked for."""
    size = path.stat().st_size
    if size > plan.limits.max_bytes:
        raise ExportRefused(
            "FILE_TOO_LARGE", f"The provider returned {size:,} bytes; the limit is {plan.limits.max_bytes:,}"
        )
    try:
        with rasterio.open(path) as src:
            if src.crs is None or src.transform.is_identity:
                raise ExportRefused(
                    "NOT_GEOREFERENCED", "The returned file has no CRS or geotransform, so it is not accepted"
                )
            info = {
                "width": src.width,
                "height": src.height,
                "band_count": src.count,
                "dtype": src.dtypes[0],
                "crs": src.crs.to_string(),
                "pixel_size_x": abs(src.transform.a),
                "pixel_size_y": abs(src.transform.e),
                "bounds_native": list(src.bounds),
                "band_descriptions": [d or "" for d in src.descriptions],
            }
    except RasterioError as exc:
        raise ExportRefused("NOT_A_GEOTIFF", f"The returned file could not be read as a GeoTIFF: {exc}") from exc
    if (
        abs(info["width"] - plan.width_px) > GRID_TOLERANCE_PX
        or abs(info["height"] - plan.height_px) > GRID_TOLERANCE_PX
    ):
        raise ExportRefused(
            "GRID_MISMATCH",
            f"Asked for {plan.width_px} x {plan.height_px} px, received {info['width']} x {info['height']}",
        )
    if info["band_count"] != len(plan.bands):
        raise ExportRefused("BANDS_MISMATCH", f"Asked for {len(plan.bands)} band(s), received {info['band_count']}")
    if info["crs"] != plan.crs:
        raise ExportRefused("CRS_MISMATCH", f"Asked for {plan.crs}, received {info['crs']}")
    return info


def run_export(
    provider: SatelliteProvider, plan: ExportPlan, scene: SceneInfo, scratch: ScratchDir, *, now: datetime | None = None
) -> ExportResult:
    """Export one scene over the plan's region into `scratch`. Never overwrites; leaves nothing behind on failure."""
    if scene.scene_id != plan.scene_id:
        raise ExportRefused("SCENE_MISMATCH", "The plan was made for a different scene")
    if scene.collection != COLLECTION_ID:
        raise ExportRefused("COLLECTION_MISMATCH", f"Only {COLLECTION_ID} is supported")
    stem = stem_for(scene, plan)
    tif, manifest_path, part = (scratch.path / f"{stem}{ext}" for ext in (".tif", ".provenance.json", ".tif.part"))
    for target in (tif, manifest_path, part):
        if target.exists():
            raise ExportRefused(
                "DESTINATION_EXISTS", f"{target.name} already exists; nothing was written or overwritten"
            )
    try:
        delivered = provider.export_geotiff(plan, scene, part)
        info = _inspect(part, plan)
        digest = _sha256(part)
        part.rename(tif)  # fails rather than replaces if the name appeared meanwhile on Windows
    except BaseException:
        if part.exists():
            part.unlink()  # our own partial file, written a moment ago
        raise
    created = (now or datetime.now(UTC)).astimezone(UTC)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "synthetic": bool(provider.synthetic),
        "provider": {"name": provider.name, **(delivered.get("service") or {})},
        "source": {
            **scene.as_dict(),
            "collection_title": COLLECTION_TITLE,
            "cloud_metric": "CLOUDY_PIXEL_PERCENTAGE (granule-level, 0-100)",
        },
        "file": {"name": tif.name, "sha256": digest, "size_bytes": tif.stat().st_size, **info},
        "request": {
            **plan.as_dict(),
            "area_of_interest_epsg4326": plan.aoi.as_geojson(),
            "provider_request": delivered.get("request", {}),
        },
        "processing": {
            "bands": list(plan.bands),
            "scene_selection": "one scene chosen by the person; no compositing",
            "cloud_masking": "none",
            "scale_factor": "none applied (digital numbers; reflectance = value / 10000)",
            "reprojection": f"to {plan.crs} at {plan.scale_m} m by the provider; resampling is the provider's default",
            "tool": {"name": "geopoc.satellite", "version": geopoc.__version__, "python": platform.python_version()},
        },
        # Synthetic pixels are not Copernicus data, so the Copernicus credit and terms must not be attached to them.
        "attribution": (
            list(delivered.get("attribution") or [])
            if provider.synthetic
            else [
                LICENCE["attribution"].format(year=scene.acquisition_time.year),
                *(delivered.get("attribution") or []),
            ]
        ),
        "licence": SYNTHETIC_LICENCE if provider.synthetic else LICENCE,
        "notes": NOTES
        + (
            ["SYNTHETIC: generated by the mock provider. These are not satellite measurements."]
            if provider.synthetic
            else []
        ),
    }
    try:
        with open(manifest_path, "x", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except FileExistsError as exc:  # pragma: no cover - checked above
        raise ExportRefused("DESTINATION_EXISTS", f"{manifest_path.name} already exists") from exc
    return ExportResult(tif, manifest_path, manifest)
