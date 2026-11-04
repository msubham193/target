"""Satellite imagery requests, and the limits that keep an export small.

Nothing here talks to any service. It validates what a person asks for (an area, a date range, a scene, a few bands),
and
works out how large the resulting GeoTIFF would be, so that a request that is too big is refused before anything is
sent.

Satellite imagery is data about the ground: calibrated measurements of a place at a time, with a known source. It is
not a
basemap. Basemap tiles are pictures drawn for display, are not meant to be analysed, and are not what this module
handles.
"""

import hashlib
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pyproj
from shapely.geometry import Polygon, box, mapping, shape

COLLECTION_ID = "COPERNICUS/S2_SR_HARMONIZED"
COLLECTION_TITLE = "Harmonized Sentinel-2 MSI Level-2A (surface reflectance)"
COLLECTION_FIRST_DATE = date(2017, 3, 28)  # per the Earth Engine catalogue; 2017-2018 coverage is not yet global
# Sentinel-2 bands the prototype may request, with their native resolution in metres. Values are stored as the provider
# stores them (unsigned 16-bit digital numbers; reflectance is the value divided by 10000). Nothing is rescaled.
BANDS: dict[str, int] = {
    "B2": 10,
    "B3": 10,
    "B4": 10,
    "B8": 10,
    "B5": 20,
    "B6": 20,
    "B7": 20,
    "B8A": 20,
    "B11": 20,
    "B12": 20,
}
DEFAULT_BANDS = ("B4", "B3", "B2")  # red, green, blue: true colour
SCALES_M = (10, 20, 60)
BYTES_PER_SAMPLE = 2  # uint16


@dataclass(frozen=True)
class ExportLimits:
    """Hard ceilings for one export. Settings may lower them, never raise them above Earth Engine's own caps."""

    max_aoi_km2: float = 100.0
    max_side_px: int = 1024
    max_pixels: int = 1_000_000
    max_bands: int = 4
    # Earth Engine's getDownloadURL refuses requests over 32 MB or with a grid side over 10000 (checked 2026-10-29).
    max_bytes: int = 16 * 1024**2
    max_days: int = 366
    max_scenes: int = 100
    search_max_aoi_km2: float = 10_000.0

    def __post_init__(self) -> None:
        if self.max_bytes > 32 * 1024**2 or self.max_side_px > 10_000:
            raise ValueError("limits may not exceed Earth Engine's own getDownloadURL caps (32 MB, 10000 px)")


DEFAULT_LIMITS = ExportLimits()


class RequestRefused(ValueError):
    """A request that is invalid or over a limit. `code` is stable; the message says what to change."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _finite(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise RequestRefused("AOI_INVALID", f"{what} must be a finite number")
    return float(value)


def geodesic_area_km2(polygon: Polygon) -> float:
    area, _ = pyproj.Geod(ellps="WGS84").geometry_area_perimeter(polygon)
    return abs(area) / 1e6


@dataclass(frozen=True)
class AreaOfInterest:
    """A simple polygon in EPSG:4326 (longitude, latitude). The export region is its bounding box."""

    polygon: Polygon

    @classmethod
    def from_bbox(cls, west: float, south: float, east: float, north: float) -> "AreaOfInterest":
        values = [
            _finite(v, n) for v, n in zip((west, south, east, north), ("west", "south", "east", "north"), strict=True)
        ]
        return cls.from_polygon(box(*values))

    @classmethod
    def from_geojson(cls, geometry: dict[str, Any]) -> "AreaOfInterest":
        if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
            raise RequestRefused(
                "AOI_INVALID", "The area of interest must be a GeoJSON Polygon (EPSG:4326, [longitude, latitude])"
            )
        for ring in geometry.get("coordinates") or []:
            for position in ring:
                if not isinstance(position, list | tuple) or len(position) != 2:
                    raise RequestRefused("AOI_INVALID", "Each position must be [longitude, latitude]")
                _finite(position[0], "longitude")
                _finite(position[1], "latitude")
        try:
            return cls.from_polygon(shape(geometry))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RequestRefused("AOI_INVALID", f"The area of interest is not a valid polygon: {exc}") from exc

    @classmethod
    def from_polygon(cls, polygon: Polygon) -> "AreaOfInterest":
        if polygon.is_empty or not polygon.is_valid or polygon.area == 0:
            raise RequestRefused("AOI_INVALID", "The area of interest is empty, self-intersecting or has no area")
        west, south, east, north = polygon.bounds
        if west < -180 or east > 180 or south < -90 or north > 90:
            raise RequestRefused("AOI_RANGE", "Longitude must be within ±180° and latitude within ±90°")
        if east - west > 180:
            raise RequestRefused("AOI_ANTIMERIDIAN", "Areas crossing the 180° meridian are not supported")
        return cls(polygon)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.polygon.bounds

    @property
    def area_km2(self) -> float:
        return geodesic_area_km2(self.polygon)

    @property
    def bbox_area_km2(self) -> float:
        return geodesic_area_km2(box(*self.bounds))

    @property
    def centre(self) -> tuple[float, float]:
        west, south, east, north = self.bounds
        return (west + east) / 2, (south + north) / 2

    def as_geojson(self) -> dict[str, Any]:
        raw = mapping(self.polygon)
        return {"type": raw["type"], "coordinates": [[list(p) for p in ring] for ring in raw["coordinates"]]}

    def bbox_geojson(self) -> dict[str, Any]:
        return AreaOfInterest(box(*self.bounds)).as_geojson()

    def fingerprint(self) -> str:
        """Short, stable identifier of the area (for file names), not a security hash."""
        return hashlib.sha256(repr([round(v, 8) for v in self.bounds]).encode()).hexdigest()[:8]


@dataclass(frozen=True)
class DateRange:
    """Inclusive on both ends, in UTC calendar days."""

    start: date
    end: date

    def check(self, limits: ExportLimits, today: date | None = None) -> "DateRange":
        today = today or datetime.now(UTC).date()
        if self.end < self.start:
            raise RequestRefused("DATES_INVALID", "The end date is before the start date")
        if self.end > today:
            raise RequestRefused("DATES_FUTURE", "The end date is in the future")
        if self.start < COLLECTION_FIRST_DATE:
            raise RequestRefused("DATES_TOO_EARLY", f"{COLLECTION_ID} starts on {COLLECTION_FIRST_DATE.isoformat()}")
        if (self.end - self.start).days + 1 > limits.max_days:
            raise RequestRefused("DATES_TOO_LONG", f"A search covers at most {limits.max_days} days")
        return self

    @property
    def end_exclusive(self) -> date:
        return self.end + timedelta(days=1)


@dataclass(frozen=True)
class SceneQuery:
    aoi: AreaOfInterest
    dates: DateRange
    max_cloud_percent: float | None = None
    limit: int = 50

    def check(self, limits: ExportLimits = DEFAULT_LIMITS, today: date | None = None) -> "SceneQuery":
        self.dates.check(limits, today)
        if self.aoi.area_km2 > limits.search_max_aoi_km2:
            raise RequestRefused("AOI_TOO_LARGE", f"A search covers at most {limits.search_max_aoi_km2:g} km²")
        if self.max_cloud_percent is not None and not 0 <= self.max_cloud_percent <= 100:
            raise RequestRefused("CLOUD_RANGE", "The cloud limit must be between 0 and 100 percent")
        if not 1 <= self.limit <= limits.max_scenes:
            raise RequestRefused("LIMIT_RANGE", f"Ask for between 1 and {limits.max_scenes} scenes")
        return self


@dataclass(frozen=True)
class SceneInfo:
    """What the catalogue says about one acquisition. Anything it does not say is None, never guessed."""

    scene_id: str
    collection: str
    acquisition_time: datetime  # UTC
    cloud_percent: float | None  # CLOUDY_PIXEL_PERCENTAGE: share of pixels flagged cloudy, 0-100
    tile: str | None = None  # MGRS tile
    processing_baseline: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "collection": self.collection,
            "acquisition_time": self.acquisition_time.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "cloud_percent": self.cloud_percent,
            "tile": self.tile,
            "processing_baseline": self.processing_baseline,
        }


def utm_crs_for(lon: float, lat: float) -> str:
    """The WGS 84 / UTM zone covering a point, as an EPSG code. (Polar areas are refused earlier by the range check.)"""
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + min(zone, 60)}"


@dataclass(frozen=True)
class ExportPlan:
    """One small export, worked out and checked before anything is requested."""

    scene_id: str
    aoi: AreaOfInterest
    bands: tuple[str, ...]
    scale_m: int
    crs: str
    width_px: int
    height_px: int
    estimated_bytes: int
    limits: ExportLimits

    @property
    def pixels(self) -> int:
        return self.width_px * self.height_px

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "bands": list(self.bands),
            "scale_m": self.scale_m,
            "crs": self.crs,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "pixels": self.pixels,
            "estimated_bytes": self.estimated_bytes,
            "region_bbox_epsg4326": self.aoi.bbox_geojson(),
            "limits": {
                "max_aoi_km2": self.limits.max_aoi_km2,
                "max_side_px": self.limits.max_side_px,
                "max_pixels": self.limits.max_pixels,
                "max_bands": self.limits.max_bands,
                "max_bytes": self.limits.max_bytes,
            },
        }


def plan_export(
    scene_id: str,
    aoi: AreaOfInterest,
    *,
    bands: tuple[str, ...] = DEFAULT_BANDS,
    scale_m: int = 10,
    limits: ExportLimits = DEFAULT_LIMITS,
) -> ExportPlan:
    """Check a request against every limit and return the plan, or raise RequestRefused saying what to change."""
    if not scene_id or not scene_id.startswith(COLLECTION_ID + "/"):
        raise RequestRefused("SCENE_INVALID", f"A scene id looks like {COLLECTION_ID}/<product>")
    if scale_m not in SCALES_M:
        raise RequestRefused("SCALE_INVALID", f"The pixel size must be one of {SCALES_M} metres")
    if not bands or len(set(bands)) != len(bands):
        raise RequestRefused("BANDS_INVALID", "Choose at least one band, each once")
    unknown = [b for b in bands if b not in BANDS]
    if unknown:
        raise RequestRefused("BANDS_INVALID", f"Unknown band(s) {unknown}; available: {sorted(BANDS)}")
    if len(bands) > limits.max_bands:
        raise RequestRefused("TOO_MANY_BANDS", f"At most {limits.max_bands} bands per export")
    finer = [b for b in bands if BANDS[b] > scale_m]
    if finer:
        # A coarser band resampled to a smaller pixel looks sharper than it is; say so rather than do it silently.
        raise RequestRefused(
            "SCALE_FINER_THAN_BAND",
            f"{finer} are native {max(BANDS[b] for b in finer)} m bands; use that scale or coarser",
        )
    if aoi.bbox_area_km2 > limits.max_aoi_km2:
        raise RequestRefused(
            "AOI_TOO_LARGE",
            f"The export region (the bounding box, {aoi.bbox_area_km2:.1f} km²) is over the {limits.max_aoi_km2:g} "
            f"km² limit",
        )
    west, south, east, north = aoi.bounds
    geod = pyproj.Geod(ellps="WGS84")
    mid = (south + north) / 2
    width_m = geod.inv(west, mid, east, mid)[2]
    height_m = geod.inv((west + east) / 2, south, (west + east) / 2, north)[2]
    width_px, height_px = max(1, math.ceil(width_m / scale_m)), max(1, math.ceil(height_m / scale_m))
    if max(width_px, height_px) > limits.max_side_px:
        raise RequestRefused(
            "TOO_WIDE",
            f"At {scale_m} m the grid would be {width_px} x {height_px} px; at most {limits.max_side_px} per side",
        )
    if width_px * height_px > limits.max_pixels:
        raise RequestRefused(
            "TOO_MANY_PIXELS", f"{width_px * height_px:,} pixels is over the {limits.max_pixels:,} limit"
        )
    estimated = width_px * height_px * len(bands) * BYTES_PER_SAMPLE
    if estimated > limits.max_bytes:
        raise RequestRefused(
            "TOO_LARGE",
            f"The export would be about {estimated / 1024**2:.1f} MB; the limit is {limits.max_bytes / 1024**2:.0f} MB",
        )
    return ExportPlan(
        scene_id=scene_id,
        aoi=aoi,
        bands=tuple(bands),
        scale_m=scale_m,
        crs=utm_crs_for(*aoi.centre),
        width_px=width_px,
        height_px=height_px,
        estimated_bytes=estimated,
        limits=limits,
    )
