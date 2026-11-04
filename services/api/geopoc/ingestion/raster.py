"""GeoTIFF validation, raster metadata and georeferencing checks.

Nothing is guessed: with a missing or invalid CRS, or without a usable geotransform, no geographic
footprint or coordinate is produced and the reason is recorded.
"""

import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyproj
import rasterio
from pyproj import Transformer
from pyproj.exceptions import CRSError, ProjError
from rasterio.errors import NotGeoreferencedWarning, RasterioError
from rasterio.windows import Window
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from geopoc.config import Settings
from geopoc.geo import antimeridian
from geopoc.geo import transform as transforms
from geopoc.geo.convert import acknowledgement_record
from geopoc.ingestion.rules import Inspection, UnreadableAsset, json_safe

SAMPLE = 512  # edge length of the windows read to prove the pixel data is decodable
EDGE_POINTS = 21  # footprint outline density per edge


def _outline_pixels(width: int, height: int) -> list[tuple[float, float]]:
    steps = np.linspace(0.0, 1.0, EDGE_POINTS)
    top = [(s * width, 0.0) for s in steps]
    right = [(float(width), s * height) for s in steps[1:]]
    bottom = [(s * width, float(height)) for s in steps[::-1][1:]]
    left = [(0.0, s * height) for s in steps[::-1][1:]]
    return top + right + bottom + left


@dataclass
class FootprintResult:
    """The outline of a raster in EPSG:4326, or the reason there is none.

    `error` is set when the outline was refused because no transformation may be used (ballpark, a missing grid, or
    several operations with none chosen). `domain_failure` is set when the coordinates do not fit the stated CRS.
    `indicative` marks an outline drawn with PROJ's default operation while several apply: it is labelled, never final.
    """

    polygon: BaseGeometry | None = None  # a Polygon, or a MultiPolygon split at 180° when `crosses_antimeridian`
    record: dict | None = None
    error: transforms.TransformationError | None = None
    domain_failure: bool = False
    indicative: bool = False
    operation: transforms.Operation | None = None
    crosses_antimeridian: bool = False
    acknowledgement: transforms.Acknowledgement | None = None  # the acknowledgement that allowed the operation


def _outline_polygon(lon, lat) -> tuple[BaseGeometry, bool] | None:
    """The outline as a footprint and whether it crosses the antimeridian, or None if it is not a valid footprint
    (non-finite, outside the range of longitude and latitude, self-intersecting or empty)."""
    return antimeridian.outline(lon, lat)


def footprint_checked(
    transform,
    width: int,
    height: int,
    crs: pyproj.CRS,
    *,
    operation_id: str | None = None,
    accept_ballpark: bool = False,
    allow_unselected: bool = False,
    acknowledgement: transforms.Acknowledgement | None = None,
) -> FootprintResult:
    """Raster outline in EPSG:4326 through an operation that may be used (see `geopoc.geo.transform`).

    Without `operation_id`, an exact or the only approximate operation is used. A ballpark operation, a missing grid, or
    a choice among several operations produces `error` instead of a quietly different outline, unless `allow_unselected`
    is set, which draws the outline with PROJ's default and marks it `indicative` (used when a file is first read, when
    nobody can choose yet). The same applies to an operation that needs an acknowledgement (accuracy worse than
    `transform.ACK_ACCURACY_M`, or not stated): without a matching `acknowledgement` it is refused, or only drawn as
    indicative.

    An outline that runs through longitude 180° is returned split into a MultiPolygon (`crosses_antimeridian`), and its
    record says so.
    """
    xs, ys = zip(*[transform @ point for point in _outline_pixels(width, height)], strict=True)
    xs, ys = np.array(xs), np.array(ys)
    try:
        coarse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        lon, lat = coarse.transform(xs, ys)
    except ProjError:
        return FootprintResult(domain_failure=True)
    if _outline_polygon(lon, lat) is None:
        return FootprintResult(domain_failure=True)
    # The default result above only says where the raster is, so that regional operations can be matched to it.
    bounds = transforms.locate(crs, xs, ys)
    try:
        resolved = transforms.resolve(crs, "EPSG:4326", bounds, operation_id, accept_ballpark, acknowledgement)
    except transforms.TransformationError as exc:
        open_question = exc.code in ("TRANSFORMATION_SELECTION_REQUIRED", transforms.ACK_REQUIRED_CODE)
        if open_question and allow_unselected and exc.plan is not None:
            polygon, crossing = _outline_polygon(lon, lat)
            needs_ack = exc.code == transforms.ACK_REQUIRED_CODE
            record = {
                "target_crs_id": "EPSG:4326",
                "definition": coarse.definition,
                "accuracy_m": exc.extra.get("accuracy_m") if needs_ack else None,
                "proj_version": pyproj.proj_version_str,
                "pyproj_version": pyproj.__version__,
                "plan_status": "acknowledgement_required" if needs_ack else "selection_required",
                "operation": None,
                "candidates": [o.id for o in exc.plan.usable],
                "note": (
                    "Indicative outline made with PROJ's default operation. The only applicable datum shift is "
                    f"{exc.extra['operation_name']} ({exc.extra['operation_id']}); it needs to be acknowledged "
                    "before it is used, and nothing has been acknowledged."
                    if needs_ack
                    else "Indicative outline made with PROJ's default operation. Several registered operations apply "
                    "and none has been chosen."
                ),
                **({"acknowledgement_required": exc.extra} if needs_ack else {}),
                **antimeridian.record_fields(crossing),
            }
            return FootprintResult(polygon, record, indicative=True, crosses_antimeridian=crossing)
        return FootprintResult(error=exc)
    operation = resolved.operation
    assert operation.transformer is not None
    out_lon, out_lat = operation.transformer.transform(xs, ys)
    outline = _outline_polygon(out_lon, out_lat)
    if outline is None:
        return FootprintResult(domain_failure=True)
    polygon, crossing = outline
    record = {
        "target_crs_id": "EPSG:4326",
        "definition": operation.pipeline,
        "accuracy_m": operation.accuracy_m,
        "proj_version": pyproj.proj_version_str,
        "pyproj_version": pyproj.__version__,
        "plan_status": resolved.plan.status,
        "operation": {
            "id": operation.id,
            "name": operation.name,
            "classification": operation.klass,
            "authority_code": operation.authority_code,
            "chosen_by": "selection" if operation_id else "only_option",
            "ballpark_accepted": resolved.accepted_ballpark,
            "requires_acknowledgement": operation.requires_acknowledgement,
            "acknowledgement": acknowledgement_record(resolved.acknowledgement),
        },
        "acknowledgement_threshold_m": transforms.ACK_ACCURACY_M,
        **antimeridian.record_fields(crossing),
    }
    return FootprintResult(
        polygon,
        record,
        operation=operation,
        crosses_antimeridian=crossing,
        acknowledgement=resolved.acknowledgement,
    )


def _footprint(transform, width: int, height: int, crs: pyproj.CRS) -> tuple[Polygon | None, dict | None]:
    """Raster outline in EPSG:4326, or None when it does not make sense or no transformation may be used."""
    result = footprint_checked(transform, width, height, crs)
    return result.polygon, result.record


def footprint(transform, width: int, height: int, crs: pyproj.CRS) -> tuple[Polygon | None, dict | None]:
    """Public name for the outline computation, shared with georeferencing recorded by a person."""
    return _footprint(transform, width, height, crs)


# The picture formats a scanned map sheet may arrive in, by content family. Only map-sheet intake passes these; an
# overhead raster upload is always a TIFF.
PICTURE_FORMATS: dict[str, tuple[str, str]] = {
    "tiff": ("GTiff", "image/tiff"),
    "png": ("PNG", "image/png"),
    "jpeg": ("JPEG", "image/jpeg"),
}


def inspect_raster(path: Path, family: str, settings: Settings) -> Inspection:
    expected_driver, media_type = PICTURE_FORMATS.get(family, PICTURE_FORMATS["tiff"])
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.open(path) as src:
                if src.driver != expected_driver:
                    raise UnreadableAsset(
                        "ASSET_UNREADABLE",
                        "The file is not a readable TIFF / GeoTIFF."
                        if expected_driver == "GTiff"
                        else f"The file is not a readable {expected_driver} picture.",
                    )
                width, height, count = src.width, src.height, src.count
                if width < 1 or height < 1 or count < 1:
                    raise UnreadableAsset("ASSET_UNREADABLE", "The raster has no pixel data.")
                # Read the first and last corners only: bounded memory, and truncation shows up at the end.
                w, h = min(SAMPLE, width), min(SAMPLE, height)
                src.read(1, window=Window(0, 0, w, h))
                src.read(count, window=Window(width - w, height - h, w, h))

                transform = src.transform
                has_geotransform = not transform.is_identity
                wkt = src.crs.to_wkt() if src.crs else ""
                raster: dict[str, Any] = {
                    "driver": src.driver,
                    "width": width,
                    "height": height,
                    "band_count": count,
                    "dtypes": list(src.dtypes),
                    "nodata": json_safe(list(src.nodatavals)),
                    "geotransform": list(transform.to_gdal()) if has_geotransform else None,
                    "bounds_native": None,
                }
                tags = {k: v for k, v in list(src.tags().items())[:50]}
                compression = src.compression.value if src.compression else None
                gcp_count = len(src.gcps[0])
                bounds = src.bounds
                overview_levels = list(src.overviews(1))
                block_shape = list(src.block_shapes[0]) if src.block_shapes else None
                band_info = [
                    {
                        "band": index + 1,
                        "description": src.descriptions[index] or None,
                        "color_interpretation": src.colorinterp[index].name,
                        "units": (src.units[index] or None) if src.units else None,
                        "scale": src.scales[index] if src.scales else None,
                        "offset": src.offsets[index] if src.offsets else None,
                    }
                    for index in range(count)
                ]
    except UnreadableAsset:
        raise
    except (RasterioError, OSError, ValueError) as exc:
        raise UnreadableAsset(
            "ASSET_UNREADABLE",
            "The file is corrupt or not a readable GeoTIFF."
            if expected_driver == "GTiff"
            else f"The file is corrupt or not a readable {expected_driver} picture.",
        ) from exc

    result = Inspection(detected_format=expected_driver, media_type=media_type)
    result.metadata = json_safe(
        {
            "tags": tags,
            "compression": compression,
            "ground_control_points": gcp_count,
            "gdal_version": rasterio.__gdal_version__,
            "overview_levels": overview_levels,
            "block_shape": block_shape,
            # As the file declares them. Band identities are never inferred from order or values.
            "bands": band_info,
        }
    )

    # --- geotransform ---------------------------------------------------------------------------------
    pixel_x = math.hypot(transform.a, transform.d)
    pixel_y = math.hypot(transform.b, transform.e)
    geotransform_ok = (
        has_geotransform
        and all(math.isfinite(v) for v in transform.to_gdal())
        and pixel_x > 0
        and pixel_y > 0
        and transform.determinant != 0
    )
    raster["pixel_size_x"] = pixel_x if geotransform_ok else None
    raster["pixel_size_y"] = pixel_y if geotransform_ok else None
    if not has_geotransform:
        result.unknowns["geotransform"] = "The file has no affine geotransform, so pixels have no map position."
    elif not geotransform_ok:
        result.unknowns["geotransform"] = "The file's geotransform is degenerate (zero or non-finite pixel size)."
    if gcp_count:
        result.unknowns["ground_control_points"] = (
            f"{gcp_count} ground control points are present but are not used for georeferencing."
        )

    # --- CRS ------------------------------------------------------------------------------------------
    crs: pyproj.CRS | None = None
    raster.update(crs_status="missing", crs_identifier=None, crs_name=None, crs_wkt=None, pixel_units=None)
    if not wkt:
        result.unknowns["crs"] = "The file declares no coordinate reference system. None is assumed."
        result.flag("MISSING_CRS")
    else:
        raster["crs_wkt"] = wkt
        try:
            parsed = pyproj.CRS.from_wkt(wkt)
        except CRSError:
            parsed = None
        if parsed is None:
            raster["crs_status"] = "invalid"
            result.unknowns["crs"] = "The file's CRS definition could not be interpreted."
            result.flag("INVALID_CRS")
        elif not (parsed.is_geographic or parsed.is_projected) or parsed.is_compound:
            raster["crs_status"] = "invalid"
            raster["crs_name"] = parsed.name
            result.unknowns["crs"] = (
                f"The file's CRS ({parsed.name!r}) is not a geographic or projected CRS tied to the Earth, "
                "so it cannot give map positions."
            )
            result.flag("INVALID_CRS")
        else:
            crs = parsed
            raster["crs_status"] = "valid"
            raster["crs_name"] = parsed.name
            authority = parsed.to_authority(min_confidence=100)
            if authority:
                raster["crs_identifier"] = f"{authority[0]}:{authority[1]}"
            else:
                result.unknowns["crs_identifier"] = (
                    "The CRS is fully defined in the file but matches no registry code exactly; "
                    "its WKT definition is kept."
                )
                result.flag("CRS_NO_AUTHORITY_CODE")

    # --- georeferencing = valid CRS + usable geotransform + coordinates that fit the CRS ---------------
    footprint = None
    raster["georeferenced"] = False
    if crs is not None and geotransform_ok:
        checked = footprint_checked(transform, width, height, crs, allow_unselected=True)
        footprint, transformation = checked.polygon, checked.record
        if footprint is None and checked.error is not None:
            # The coordinates are fine, but no transformation to longitude/latitude may be used. Nothing is placed.
            result.unknowns["transformation"] = f"{checked.error.title}. {checked.error.detail}"
            result.unknowns["georeferencing"] = (
                "The raster has a CRS and a geotransform, but the datum transformation needed to place it on a map is "
                "not available, so it is not placed. See the CRS assessment."
            )
            result.flag("TRANSFORMATION_BLOCKED")
            result.flag(checked.error.code)
        elif footprint is None:
            result.unknowns["georeferencing"] = (
                "The raster's coordinates fall outside the valid domain of its stated CRS, so the "
                "georeferencing is not trusted."
            )
            result.flag("INVALID_GEOREFERENCING")
        else:
            raster["georeferenced"] = True
            raster["pixel_units"] = crs.axis_info[0].unit_name
            raster["bounds_native"] = {
                "left": bounds.left,
                "bottom": bounds.bottom,
                "right": bounds.right,
                "top": bounds.top,
            }
            result.metadata["footprint_transformation"] = transformation
            if checked.indicative and (transformation or {}).get("plan_status") == "acknowledgement_required":
                needed = transformation["acknowledgement_required"]
                accuracy = needed.get("accuracy_m")
                result.unknowns["transformation"] = (
                    f"The only applicable datum shift is {needed['operation_name']} ({needed['operation_id']}), "
                    + (
                        f"with a documented accuracy of about {accuracy:g} m, worse than "
                        f"{transforms.ACK_ACCURACY_M:g} m"
                        if accuracy is not None
                        else "whose accuracy is not stated"
                    )
                    + ". It is not used until it has been acknowledged, and an acknowledgement cannot be recorded "
                    "for a "
                    "raster georeferenced by its own file. The outline shown is indicative only (PROJ's default)."
                )
                result.flag(transforms.ACK_REQUIRED_CODE)
            elif checked.indicative:
                result.unknowns["transformation"] = (
                    "Several registered datum transformations apply to this raster and none has been chosen. The "
                    "outline shown is indicative only (PROJ's default). Choose a transformation to place it on a map."
                )
                result.flag("TRANSFORMATION_SELECTION_REQUIRED")
            elif checked.operation is not None and checked.operation.klass == "approximate":
                result.unknowns["transformation_accuracy"] = (
                    f"Placed with the registered datum shift {checked.operation.name} "
                    f"({checked.operation.id}); documented accuracy about {checked.operation.accuracy_m:g} m."
                )
                result.flag("TRANSFORMATION_APPROXIMATE")
            if checked.crosses_antimeridian:
                result.unknowns["antimeridian"] = (
                    "The raster crosses longitude 180°. Its footprint is stored split into two parts at the "
                    "antimeridian. Measurement, areas of interest, terrain, pixel lookup and tiles are not supported "
                    "for it."
                )
                result.flag("CROSSES_ANTIMERIDIAN")
    if not raster["georeferenced"]:
        result.flag("NOT_GEOREFERENCED")
        result.unknowns["pixel_units"] = "Without valid georeferencing the ground size of a pixel is unknown."
        result.unknowns.setdefault(
            "georeferencing", "The raster cannot be placed on a map; it can only be viewed in image space."
        )

    tiff_time = tags.get("TIFFTAG_DATETIME")
    result.unknowns["acquisition_time"] = (
        "The TIFF date tag does not state what it measures or its time zone; it is kept as raw metadata."
        if tiff_time
        else "The file carries no acquisition time."
    )
    result.flag("TIMESTAMP_UNKNOWN")

    raster["footprint"] = footprint
    result.raster = raster
    return result
