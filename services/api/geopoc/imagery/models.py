"""Neutral search and scene models. Nothing here talks to any service or knows a particular provider.

A scene is what a catalogue says about one acquisition. Anything the catalogue does not say is None, never guessed.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from geopoc.imagery.aoi import Aoi
from geopoc.imagery.errors import ImageryRefused

# The first Sentinel-2 satellite launched on this date; no Sentinel-2 scene can be older.
EARLIEST_DATE = date(2015, 6, 23)


@dataclass(frozen=True)
class ImageryLimits:
    """Ceilings for one search. They keep a request small and the answer bounded."""

    max_aoi_km2: float = 10_000.0
    max_days: int = 366
    max_limit: int = 100


DEFAULT_LIMITS = ImageryLimits()


@dataclass(frozen=True)
class SearchQuery:
    provider: str
    collection: str
    aoi: Aoi
    start: date
    end: date  # inclusive, UTC calendar days
    max_cloud_percent: float | None = None
    limit: int = 20
    page_token: str | None = None

    def check(self, limits: ImageryLimits = DEFAULT_LIMITS, today: date | None = None) -> "SearchQuery":
        today = today or datetime.now(UTC).date()
        if self.end < self.start:
            raise ImageryRefused("DATES_INVALID", "The end date is before the start date")
        if self.end > today:
            raise ImageryRefused("DATES_FUTURE", "The end date is in the future")
        if self.start < EARLIEST_DATE:
            raise ImageryRefused("DATES_TOO_EARLY", f"No Sentinel-2 scene is older than {EARLIEST_DATE.isoformat()}")
        if (self.end - self.start).days + 1 > limits.max_days:
            raise ImageryRefused("DATES_TOO_LONG", f"A search covers at most {limits.max_days} days")
        if self.aoi.area_km2 > limits.max_aoi_km2:
            raise ImageryRefused("AOI_TOO_LARGE", f"A search covers at most {limits.max_aoi_km2:g} km²")
        if self.max_cloud_percent is not None and not (0 <= self.max_cloud_percent <= 100):
            raise ImageryRefused("CLOUD_RANGE", "The cloud limit must be between 0 and 100 percent")
        if not 1 <= self.limit <= limits.max_limit:
            raise ImageryRefused("LIMIT_RANGE", f"Ask for between 1 and {limits.max_limit} scenes per page")
        return self


@dataclass(frozen=True)
class AssetRef:
    """A named file of a scene as listed by the catalogue. Its address is not kept: nothing is downloaded."""

    key: str
    title: str | None
    media_type: str | None
    roles: list[str]


@dataclass(frozen=True)
class SceneItem:
    provider: str
    collection: str
    scene_id: str
    acquisition_time: datetime  # UTC
    cloud_percent: float | None  # share of cloudy pixels in the whole granule, 0-100, as the catalogue states it
    platform: str | None = None
    constellation: str | None = None
    instruments: list[str] = field(default_factory=list)
    processing_level: str | None = None
    processing_baseline: str | None = None
    mgrs_tile: str | None = None
    epsg: int | None = None
    gsd_m: float | None = None
    relative_orbit: int | None = None
    geometry: dict[str, Any] | None = None  # the footprint, EPSG:4326
    bbox: list[float] | None = None
    aoi_check: str = "not_verified"  # "intersects" when the footprint was checked against the exact area
    assets: list[AssetRef] = field(default_factory=list)


@dataclass
class Filtering:
    """What the local checks removed from the provider's page, so a short page is explained."""

    server_returned: int = 0
    outside_aoi: int = 0
    above_cloud_limit: int = 0
    unknown_cloud: int = 0
    malformed: int = 0
    kept: int = 0


@dataclass
class Page:
    """One page of a search, as the provider adapter returns it."""

    items: list[SceneItem]
    filtering: Filtering
    matched: int | None
    next_token: str | None
    notes: list[str] = field(default_factory=list)
