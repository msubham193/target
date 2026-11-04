"""Optional area of interest. User-entered context: it never positions or proves the location of an asset."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shapely
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from geopoc.geo import convert
from geopoc.geo.crs import normalise_identifier, resolve
from geopoc.geo.transform import ACK_REQUIRED_CODE, Acknowledgement, TransformationError

ALLOWED_TYPES = {"Point", "Polygon", "MultiPolygon"}
STORAGE_CRS = "EPSG:4326"


class AoiError(ValueError):
    def __init__(self, code: str, message: str, transformation: TransformationError | None = None) -> None:
        super().__init__(message)
        self.code = code
        # Set when the AOI could not be moved to WGS 84 because no datum transformation may be used for it.
        self.transformation = transformation


@dataclass
class Aoi:
    geom_4326: BaseGeometry
    source: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def build_aoi(
    geometry: dict[str, Any],
    crs_id: str,
    operation_id: str | None = None,
    acknowledge_accuracy: bool = False,
    acknowledged_by: dict[str, Any] | None = None,
) -> Aoi:
    """Validate an AOI given in `crs_id` (x/y order: easting or longitude first) and derive an EPSG:4326 copy.

    The geometry is never repaired or altered; invalid input is rejected. The move to WGS 84 follows the datum
    transformation policy: exact or the single registered shift is used, several need `operation_id`, and a ballpark
    or missing-grid conversion is refused with the options. A shift whose accuracy is worse than 10 m, or not stated,
    is refused until `acknowledge_accuracy` is true; the acknowledgement (operation, CRSs, accuracy, who, when) is then
    stored in the AOI's own source record.
    """
    crs = resolve(crs_id)
    if not (crs.is_geographic or crs.is_projected) or crs.is_compound:
        raise AoiError("AOI_CRS_UNSUITABLE", "The AOI CRS must be a 2D geographic or projected CRS.")

    if geometry.get("type") not in ALLOWED_TYPES:
        raise AoiError("AOI_GEOMETRY_INVALID", "AOI geometry must be a Point, Polygon or MultiPolygon.")
    try:
        geom = shape(geometry)
    except Exception as exc:  # shapely raises several unrelated types for malformed input
        raise AoiError("AOI_GEOMETRY_INVALID", "AOI geometry is not valid GeoJSON.") from exc
    if geom.is_empty:
        raise AoiError("AOI_GEOMETRY_INVALID", "AOI geometry is empty.")
    if not np.isfinite(shapely.get_coordinates(geom)).all():
        raise AoiError("AOI_GEOMETRY_INVALID", "AOI coordinates must be finite numbers.")
    if not geom.is_valid:
        raise AoiError("AOI_GEOMETRY_INVALID", f"AOI geometry is invalid: {explain_validity(geom)}.")

    provenance: dict[str, Any] = {}

    def to_storage_crs(xy: np.ndarray) -> np.ndarray:
        try:
            converted = convert.convert(crs, STORAGE_CRS, xy[:, 0], xy[:, 1], operation_id=operation_id)
        except TransformationError as exc:
            # The person acknowledges exactly what was refused: the acknowledgement is built from the refusal itself,
            # so it names that operation, those CRSs and that accuracy, and nothing else.
            if exc.code != ACK_REQUIRED_CODE or not acknowledge_accuracy:
                raise
            given = Acknowledgement(
                operation_id=exc.extra["operation_id"],
                source_crs=exc.extra["source_crs"],
                target_crs=exc.extra["target_crs"],
                accuracy_m=exc.extra["accuracy_m"],
                scope="stored",
            )
            converted = convert.convert(
                crs, STORAGE_CRS, xy[:, 0], xy[:, 1], operation_id=operation_id, acknowledgement=given
            )
        provenance.update(converted.provenance)
        return np.column_stack([converted.x, converted.y])

    try:
        geom_4326 = shapely.transform(geom, to_storage_crs)
    except TransformationError as exc:
        if exc.code == convert.TRANSFORM_FAILED:
            raise AoiError(
                "AOI_COORDINATES_OUT_OF_RANGE",
                "The AOI coordinates are outside the valid range of the stated CRS. Check the CRS and the "
                "coordinate order (x / easting / longitude first).",
            ) from exc
        raise AoiError(exc.code, f"{exc.title}. {exc.detail}", exc) from exc
    coords = shapely.get_coordinates(geom_4326)
    if not np.isfinite(coords).all() or (np.abs(coords[:, 0]) > 180).any() or (np.abs(coords[:, 1]) > 90).any():
        raise AoiError(
            "AOI_COORDINATES_OUT_OF_RANGE",
            "The AOI coordinates are outside the valid range of the stated CRS. Check the CRS and the "
            "coordinate order (x / easting / longitude first).",
        )

    warnings: list[str] = []
    area = crs.area_of_use
    if area and area.west <= area.east:
        if not box(area.west, area.south, area.east, area.north).covers(geom_4326):
            warnings.append("AOI_OUTSIDE_CRS_AREA_OF_USE")

    recorded = provenance.get("operation", {}).get("acknowledgement")
    if recorded is not None:
        recorded = {**recorded, **(acknowledged_by or {})}
        provenance["operation"]["acknowledgement"] = recorded
        provenance["acknowledgement"] = recorded  # where convert.acknowledgement_from_record() looks
    source = {
        "geometry": geometry,
        "crs_id": normalise_identifier(crs_id),
        "coordinate_order": "x_y",
        "transformation": {**provenance, "description": provenance["operation"]["name"]},
        "warnings": warnings,
    }
    return Aoi(geom_4326=geom_4326, source=source, warnings=warnings)
