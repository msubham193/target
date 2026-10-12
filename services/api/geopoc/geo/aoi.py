"""Optional area of interest. User-entered context: it never positions or proves the location of an asset."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyproj
import shapely
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from geopoc.geo.crs import normalise_identifier, resolve

ALLOWED_TYPES = {"Point", "Polygon", "MultiPolygon"}
STORAGE_CRS = "EPSG:4326"


class AoiError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Aoi:
    geom_4326: BaseGeometry
    source: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def build_aoi(geometry: dict[str, Any], crs_id: str) -> Aoi:
    """Validate an AOI given in `crs_id` (x/y order: easting or longitude first) and derive an EPSG:4326 copy.

    The geometry is never repaired or altered; invalid input is rejected.
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

    transformer = Transformer.from_crs(crs, STORAGE_CRS, always_xy=True)

    def to_storage_crs(xy: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(xy[:, 0], xy[:, 1])
        return np.column_stack([x, y])

    geom_4326 = shapely.transform(geom, to_storage_crs)
    coords = shapely.get_coordinates(geom_4326)
    if (
        not np.isfinite(coords).all()
        or (np.abs(coords[:, 0]) > 180).any()
        or (np.abs(coords[:, 1]) > 90).any()
    ):
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

    accuracy = transformer.accuracy
    source = {
        "geometry": geometry,
        "crs_id": normalise_identifier(crs_id),
        "coordinate_order": "x_y",
        "transformation": {
            "target_crs_id": STORAGE_CRS,
            "definition": transformer.definition,
            "description": transformer.description,
            "accuracy_m": accuracy if accuracy is not None and accuracy >= 0 else None,
            "proj_version": pyproj.proj_version_str,
            "pyproj_version": pyproj.__version__,
        },
        "warnings": warnings,
    }
    return Aoi(geom_4326=geom_4326, source=source, warnings=warnings)
