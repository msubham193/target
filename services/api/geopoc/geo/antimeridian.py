"""Rasters whose footprint crosses the antimeridian (longitude 180° = -180°).

A footprint is a list of longitudes and latitudes, and longitude wraps. A raster that straddles 180° comes out of PROJ
with some corners near +180 and others near -180, and a polygon drawn through those points would run the long way
round the earth. This module recognises that case and handles it in one defined way:

* the outline is unwrapped (longitudes continue past 180), checked for validity there, and then
* split at 180° into a MultiPolygon with one part on each side, so every coordinate stays inside [-180, 180].

Only the footprint is supported. Anything that needs one continuous area or distance on the ground (measurement, an
area of interest, terrain, pixel lookup, alignment) refuses a crossing raster with `ANTIMERIDIAN_CROSSING_UNSUPPORTED`
instead of producing a wrong answer. Tiles for a crossing raster are deferred.

A footprint that only touches 180° or -180° with an edge is not a crossing: its points are snapped to the side the rest
of the outline is on.
"""

from collections.abc import Sequence
from typing import Any

import numpy as np
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry

CROSSING_CODE = "ANTIMERIDIAN_CROSSING_UNSUPPORTED"
SPLIT_BOUNDARY_DEGREES = 180
REPRESENTATION = "split_multipolygon"
EPS = 1e-9  # degrees (about 0.1 mm): closer than this to ±180 counts as on the meridian
MAX_SPAN = 180.0  # a footprint wider than this in longitude is not classified as a crossing: it is not a raster tile


class AntimeridianUnsupported(Exception):
    """A crossing raster was given to something that cannot handle one. Mapped to a 422 problem by the API."""

    status = 422
    code = CROSSING_CODE
    title = "This raster crosses the antimeridian"

    def __init__(self, operation: str) -> None:
        self.operation = operation
        self.detail = (
            f"{operation} is not supported for a raster that crosses longitude 180°. Its footprint is recorded and "
            "split at the antimeridian, but a distance, area, height or pixel position needs one continuous area on "
            "the ground. Nothing was calculated and nothing was dropped."
        )
        super().__init__(self.title)


def refuse(operation: str) -> None:
    """Raise `AntimeridianUnsupported` for `operation`."""
    raise AntimeridianUnsupported(operation)


def normalise(lons: Sequence[float] | np.ndarray) -> np.ndarray | None:
    """Longitudes folded into [-180, 180], or None if they cannot be a footprint.

    Values slightly past ±180 are accepted only when the outline itself runs through the antimeridian (it starts on one
    side and ends within a half-turn on the other): a raster declared in plain longitude/latitude that spans 179.5° to
    180.5° is a crossing. Anything else outside ±180, such as a projected coordinate mistaken for degrees or a 0-360
    grid, is not wrapped into range: wrapping it would turn an error into a plausible-looking position."""
    values = np.asarray(lons, dtype=float)
    if not np.isfinite(values).all():
        return None
    if (np.abs(values) <= 180 + EPS).all():
        return np.clip(values, -180.0, 180.0)
    low, high = values.min(), values.max()
    if low <= 180 + EPS and 180 < high <= 360 and high - low <= MAX_SPAN:
        return np.where(values > 180, values - 360.0, values)
    if high >= -180 - EPS and -360 <= low < -180 and high - low <= MAX_SPAN:
        return np.where(values < -180, values + 360.0, values)
    return None


def _snap(values: np.ndarray) -> np.ndarray:
    """Points on the meridian itself take the side the other points are on (an edge exactly at 180 may be reported as
    180 or -180 depending on rounding)."""
    on_meridian = np.abs(np.abs(values) - 180.0) <= EPS
    if not on_meridian.any():
        return values
    rest = values[~on_meridian]
    side = 180.0 if rest.size == 0 or np.mean(rest) >= 0 else -180.0
    return np.where(on_meridian, side, values)


def crosses(lons: Sequence[float] | np.ndarray) -> bool:
    """True if an outline with these longitudes (already within ±180) runs through the antimeridian.

    The outline's points sit on a circle of longitude. The smallest arc that covers them runs between two neighbours
    across the largest gap; the outline crosses 180° exactly when that largest gap is not the one that contains 180°.
    Points that only touch the meridian never make a crossing."""
    values = np.asarray(lons, dtype=float)
    if values.size < 2 or not np.isfinite(values).all():
        return False
    values = _snap(values)
    on_meridian = np.abs(np.abs(values) - 180.0) <= EPS
    interior = np.sort(values[~on_meridian])
    if interior.size < 2:
        return False
    gaps = np.diff(interior)
    wrap_gap = interior[0] + 360.0 - interior[-1]  # the gap that contains 180
    widest_inside = float(gaps.max())
    span = 360.0 - widest_inside  # width of the arc covering the points if the widest gap is inside
    return bool(widest_inside > wrap_gap + EPS and span <= MAX_SPAN)


def unwrap(lons: Sequence[float] | np.ndarray) -> np.ndarray:
    """Continuous longitudes for an outline that crosses the antimeridian: western-hemisphere values gain 360 so that
    the outline is one unbroken run (for example 179.6 ... 180.4 instead of 179.6 ... -179.6). An outline that does not
    cross is returned unchanged apart from snapping points on the meridian."""
    values = np.asarray(lons, dtype=float)
    values = _snap(values)
    if not crosses(values):
        return values
    return np.where(values < 0, values + 360.0, values)


def split_polygon(polygon: Polygon) -> MultiPolygon:
    """Split a polygon in unwrapped longitudes at 180° into a MultiPolygon with every part inside [-180, 180]."""
    minx, miny, maxx, maxy = polygon.bounds
    if minx >= 180.0 or maxx <= 180.0:
        shift = -360.0 if minx >= 180.0 else 0.0
        moved = affinity.translate(polygon, xoff=shift) if shift else polygon
        return MultiPolygon([moved]) if isinstance(moved, Polygon) else MultiPolygon(list(moved.geoms))
    west = polygon.intersection(box(minx, miny, 180.0, maxy))
    east = polygon.intersection(box(180.0, miny, maxx, maxy))
    east = affinity.translate(east, xoff=-360.0)
    parts: list[Polygon] = []
    for piece in (west, east):
        geoms = [piece] if isinstance(piece, Polygon) else list(getattr(piece, "geoms", []))
        parts.extend(g for g in geoms if isinstance(g, Polygon) and g.area > 0)
    return MultiPolygon(parts)


def outline(lons: Sequence[float] | np.ndarray, lats: Sequence[float] | np.ndarray) -> tuple[BaseGeometry, bool] | None:
    """The footprint for an outline, and whether it crosses the antimeridian; None if it is not a valid footprint.

    Latitudes must be within ±90. A crossing outline is built unwrapped, checked there, and returned split."""
    lat_values = np.asarray(lats, dtype=float)
    if not np.isfinite(lat_values).all() or (np.abs(lat_values) > 90).any():
        return None
    folded = normalise(lons)
    if folded is None:
        return None
    unwrapped = unwrap(folded)
    crossing = crosses(folded)
    polygon = Polygon(zip(unwrapped.tolist(), lat_values.tolist(), strict=True))
    if polygon.is_empty or not polygon.is_valid or polygon.area == 0:
        return None
    if not crossing:
        return polygon, False
    split = split_polygon(polygon)
    if split.is_empty or not split.is_valid:
        return None
    return split, True


def geometry_crosses(geometry: BaseGeometry | None) -> bool:
    """Whether a stored footprint is a split crossing footprint, derived from the geometry alone: a MultiPolygon with
    one part touching +180 and another touching -180. Used where the stored flag is not available (a file's footprint
    column has no provenance beside it that a later read could be sure belongs to the same geometry)."""
    if not isinstance(geometry, MultiPolygon) or len(geometry.geoms) < 2:
        return False
    east = any(abs(g.bounds[2] - 180.0) <= EPS for g in geometry.geoms)
    west = any(abs(g.bounds[0] + 180.0) <= EPS for g in geometry.geoms)
    return east and west


def record_fields(crossing: bool) -> dict[str, Any]:
    """What the footprint's provenance record says about the antimeridian."""
    if not crossing:
        return {"crosses_antimeridian": False}
    return {
        "crosses_antimeridian": True,
        "footprint_representation": REPRESENTATION,
        "split_boundary_degrees": SPLIT_BOUNDARY_DEGREES,
    }
