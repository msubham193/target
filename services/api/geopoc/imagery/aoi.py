"""The area of interest for a catalogue search: a simple polygon in EPSG:4326 (longitude, latitude), validated locally.

Only WGS 84 longitude/latitude is accepted, so no coordinate conversion happens here and the project's shared
transformation rules (geopoc.geo.convert) are neither used nor bypassed. What leaves the machine is a bounding box
rounded outward to a coarse grid, never the exact polygon; the exact polygon filters the results locally.
"""

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import pyproj
from shapely.geometry import Polygon, box

from geopoc.imagery.errors import ImageryRefused

MAX_VERTICES = 1000
MAX_RINGS = 20


@dataclass(frozen=True)
class Aoi:
    polygon: Polygon

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.polygon.bounds

    @property
    def area_km2(self) -> float:
        area, _ = pyproj.Geod(ellps="WGS84").geometry_area_perimeter(self.polygon)
        return abs(area) / 1e6


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ImageryRefused("AOI_INVALID", f"{what} must be a finite number")
    return float(value)


def parse_aoi(geometry: Any) -> Aoi:
    """A GeoJSON Polygon in EPSG:4326, [longitude, latitude]. Raises ImageryRefused saying what to change."""
    if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
        raise ImageryRefused(
            "AOI_INVALID", "The area of interest must be a GeoJSON Polygon (EPSG:4326, [longitude, latitude])"
        )
    rings = geometry.get("coordinates")
    if not isinstance(rings, list) or not rings or not all(isinstance(r, list) for r in rings):
        raise ImageryRefused("AOI_INVALID", "The polygon needs a list of rings of [longitude, latitude] positions")
    if len(rings) > MAX_RINGS or sum(len(r) for r in rings) > MAX_VERTICES:
        raise ImageryRefused(
            "AOI_TOO_COMPLEX", f"At most {MAX_RINGS} rings and {MAX_VERTICES} positions are accepted for a search"
        )
    parsed: list[list[tuple[float, float]]] = []
    for ring in rings:
        points = []
        for position in ring:
            if not isinstance(position, list | tuple) or len(position) != 2:
                raise ImageryRefused("AOI_INVALID", "Each position must be [longitude, latitude]")
            lon, lat = _number(position[0], "longitude"), _number(position[1], "latitude")
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ImageryRefused("AOI_RANGE", "Longitude must be within ±180° and latitude within ±90°")
            points.append((lon, lat))
        if len(points) < 3:
            raise ImageryRefused("AOI_INVALID", "Each ring needs at least three positions")
        parsed.append(points)
    try:
        polygon = Polygon(parsed[0], parsed[1:])
    except (ValueError, TypeError) as exc:
        raise ImageryRefused("AOI_INVALID", f"The area of interest is not a valid polygon: {exc}") from None
    if polygon.is_empty or not polygon.is_valid or polygon.area == 0:
        raise ImageryRefused("AOI_INVALID", "The area of interest is empty, self-intersecting or has no area")
    west, _, east, _ = polygon.bounds
    if east - west > 180:
        raise ImageryRefused("AOI_ANTIMERIDIAN", "Areas crossing the 180° meridian are not supported")
    return Aoi(polygon)


def rounded_bbox(bounds: tuple[float, float, float, float], step: float) -> list[float]:
    """[west, south, east, north] rounded OUTWARD to a grid of `step` degrees, so the box always contains the area."""
    step = min(1.0, max(0.001, step if math.isfinite(step) else 0.01))
    west, south, east, north = bounds
    out = [
        math.floor(west / step + 1e-9) * step,
        math.floor(south / step + 1e-9) * step,
        math.ceil(east / step - 1e-9) * step,
        math.ceil(north / step - 1e-9) * step,
    ]
    out = [round(v, 6) for v in out]
    out[0], out[2] = max(-180.0, out[0]), min(180.0, out[2])
    out[1], out[3] = max(-90.0, out[1]), min(90.0, out[3])
    return out


def bbox_polygon(bbox: list[float]) -> Polygon:
    return box(*bbox)


def fingerprint(bbox: list[float]) -> str:
    """Short, stable label of the box that was sent, for the log. Not a security hash, and not the coordinates."""
    return hashlib.sha256(repr([round(v, 6) for v in bbox]).encode()).hexdigest()[:8]
