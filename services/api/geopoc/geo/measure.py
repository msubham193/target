"""Distance and area measurement, only where the spatial reference supports it.

Two inputs:
  lonlat   points in WGS 84 longitude/latitude (from the map). Measured geodesically on the WGS 84 ellipsoid.
  pixel    points in a raster's pixel grid (column, row; top-left corner origin). Measured only when the raster's
           effective georeferencing is valid: pixels are mapped through its affine transform into its CRS, then
           measured geodesically on that CRS's own ellipsoid. Projected CRSs also get a planar value in the CRS's
           units, labelled as including map-projection distortion. Without valid georeferencing the result is
           `unavailable` and only a length in pixels is given, labelled as image pixels, not ground units.

The method, units and ellipsoid are always reported. A measurement is of the points given, not of any feature.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import pyproj
from affine import Affine
from shapely.geometry import Polygon

from geopoc.geo import convert
from geopoc.geo.georeference import Effective
from geopoc.geo.transform import Acknowledgement, TransformationError

Kind = Literal["distance", "area"]
MAX_POINTS = 1000
WGS84 = pyproj.CRS.from_epsg(4326)


class MeasureError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Result:
    kind: Kind
    validity: Literal["valid", "unavailable"]
    value: float | None
    unit: str | None
    method: str
    perimeter_m: float | None = None
    planar: dict[str, Any] | None = None
    pixel_value: float | None = None
    pixel_unit: str | None = None
    flags: list[str] = field(default_factory=list)
    note: str = ""
    points_lonlat: list[list[float]] | None = None
    points_lonlat_note: str | None = None
    transformation: dict[str, Any] | None = None


def _check_points(kind: Kind, points: list[list[float]]) -> None:
    need = 2 if kind == "distance" else 3
    if len(points) < need:
        raise MeasureError("TOO_FEW_POINTS", f"A {kind} needs at least {need} points.")
    if len(points) > MAX_POINTS:
        raise MeasureError("TOO_MANY_POINTS", f"At most {MAX_POINTS} points can be measured at once.")
    for point in points:
        if len(point) != 2 or not all(isinstance(v, int | float) and math.isfinite(v) for v in point):
            raise MeasureError("POINT_INVALID", "Every point must be two finite numbers.")


def _polygon_ok(points: list[tuple[float, float]]) -> None:
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.area == 0:
        raise MeasureError("POLYGON_INVALID", "The outline crosses itself or encloses no area; nothing is measured.")


def _geodesic(kind: Kind, geod: pyproj.Geod, lons: list[float], lats: list[float]) -> tuple[float, float | None]:
    if kind == "distance":
        return float(geod.line_length(lons, lats)), None
    area, perimeter = geod.polygon_area_perimeter(lons, lats)
    return abs(float(area)), float(perimeter)


def measure_lonlat(kind: Kind, points: list[list[float]]) -> Result:
    _check_points(kind, points)
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    if any(abs(lon) > 180 for lon in lons) or any(abs(lat) > 90 for lat in lats):
        raise MeasureError("POINT_INVALID", "Longitudes must be within ±180° and latitudes within ±90°.")
    if any(abs(lons[i + 1] - lons[i]) > 180 for i in range(len(lons) - 1)):
        raise MeasureError("ANTIMERIDIAN", "Lines crossing the 180° meridian are not supported.")
    if kind == "area":
        _polygon_ok(list(zip(lons, lats, strict=True)))
    value, perimeter = _geodesic(kind, WGS84.get_geod(), lons, lats)
    return Result(
        kind=kind,
        validity="valid",
        value=value,
        unit="m" if kind == "distance" else "m²",
        method="geodesic on the WGS 84 ellipsoid (PROJ geodesic, Karney)",
        perimeter_m=perimeter,
        note="Measured between the points placed on the map; it is not a measurement of any feature.",
        points_lonlat=[list(p) for p in points],
    )


def measure_pixels(
    kind: Kind,
    points: list[list[float]],
    width: int,
    height: int,
    state: Effective,
    reason: str | None,
    operation_id: str | None = None,
    acknowledgement: Acknowledgement | None = None,
) -> Result:
    _check_points(kind, points)
    for col, row in points:
        if not (0 <= col <= width and 0 <= row <= height):
            raise MeasureError("POINT_OUTSIDE_RASTER", f"Pixel points must lie within 0–{width} × 0–{height}.")
    if kind == "area":
        _polygon_ok([(c, r) for c, r in points])
    pixel_polygon = Polygon(points) if kind == "area" else None
    pixel_value = (
        sum(
            math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]) for i in range(len(points) - 1)
        )
        if kind == "distance"
        else pixel_polygon.area
    )
    pixel_unit = "pixels" if kind == "distance" else "pixels²"
    if not state.georeferenced or not state.crs_wkt or not state.geotransform:
        return Result(
            kind=kind,
            validity="unavailable",
            value=None,
            unit=None,
            method="none: the raster has no valid georeferencing",
            pixel_value=pixel_value,
            pixel_unit=pixel_unit,
            flags=["NOT_GEOREFERENCED"],
            note=" ".join(
                part
                for part in (
                    "No ground measurement is possible.",
                    reason,
                    "The pixel value is in image pixels, not ground units.",
                )
                if part
            ),
        )
    crs = pyproj.CRS.from_wkt(state.crs_wkt)
    transform = Affine.from_gdal(*state.geotransform)
    native = [transform @ (c, r) for c, r in points]
    # Within the CRS's own datum: the inverse projection to its own geographic CRS. No datum changes, so no
    # transformation choice is involved, and the geodesic below is on the raster's own ellipsoid.
    to_geographic = pyproj.Transformer.from_crs(crs, crs.geodetic_crs, always_xy=True)
    lons, lats = to_geographic.transform([p[0] for p in native], [p[1] for p in native])
    value, perimeter = _geodesic(kind, crs.get_geod(), list(lons), list(lats))
    ellipsoid = crs.ellipsoid.name if crs.ellipsoid else "the CRS's ellipsoid"
    planar = None
    if crs.is_projected:
        axis = crs.axis_info[0]
        factor = axis.unit_conversion_factor or 1.0
        if kind == "distance":
            planar_native = sum(
                math.hypot(native[i + 1][0] - native[i][0], native[i + 1][1] - native[i][1])
                for i in range(len(native) - 1)
            )
            planar = {"value": planar_native * factor, "unit": "m"}
        else:
            planar = {"value": Polygon(native).area * factor * factor, "unit": "m²"}
        planar["method"] = f"planar in {crs.name} ({axis.unit_name}); includes map-projection distortion"
    # Longitude/latitude of the points on the map uses the datum transformation policy. If it may not be used the
    # measurement above still stands (it never needed WGS 84) and the points carry no longitude/latitude.
    points_lonlat = transformation = lonlat_note = None
    flags = ["CRS_USER_ASSERTED"] if state.source == "recorded" else []
    try:
        converted = convert.to_geographic(
            crs,
            [p[0] for p in native],
            [p[1] for p in native],
            operation_id=operation_id,
            acknowledgement=acknowledgement,
        )
        points_lonlat = [[float(a), float(b)] for a, b in zip(converted.x, converted.y, strict=True)]
        transformation = converted.provenance
    except TransformationError as exc:
        lonlat_note = convert.unavailable_reason(exc)
        flags.append(exc.code)
    return Result(
        kind=kind,
        validity="valid",
        value=value,
        unit="m" if kind == "distance" else "m²",
        method=f"pixel grid → {crs.name} via the affine geotransform → geodesic on the {ellipsoid} ellipsoid",
        perimeter_m=perimeter,
        planar=planar,
        pixel_value=pixel_value,
        pixel_unit=pixel_unit,
        flags=flags,
        note=(
            "Computed from georeferencing recorded by a person; its correctness is not established by the software. "
            if flags
            else ""
        )
        + "Measured between the points given; it is not a measurement of any feature.",
        points_lonlat=points_lonlat,
        points_lonlat_note=lonlat_note,
        transformation=transformation,
    )
