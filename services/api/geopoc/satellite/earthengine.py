"""The Google Earth Engine scene-search and export provider (GEE-2 and GEE-4 material). NOT TESTED AGAINST THE LIVE
SERVICE: written from the documentation, and exercised only with a stand-in `ee` module (tests/test_satellite.py).

Since GEE-1 `EarthEngineProvider.from_settings` is closed: it applies the global switches and the operating-model-A
checks (geopoc.earth_engine.runtime) and then refuses with EE_PHASE_NOT_ENABLED before `ee` is imported, so this module
cannot send an area of interest or export anything yet. Authentication is handled by geopoc.earth_engine.client
(organisation-owned identities only); the key-file route this prototype used is gone.

No token, key or signed download URL is ever logged, printed or put in an exception message (gate.redact).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from geopoc.config import Settings
from geopoc.satellite.gate import ProviderError, ProviderUnavailable, ensure_enabled, redact
from geopoc.satellite.models import COLLECTION_ID, ExportPlan, SceneInfo, SceneQuery

logger = logging.getLogger("geopoc.satellite")

TIFF_MAGIC = (b"II*\x00", b"MM\x00*")
DOWNLOAD_TIMEOUT_S = 120.0

Downloader = Callable[[str, Path, int], None]


def http_download(url: str, destination: Path, max_bytes: int) -> None:
    """Stream a signed URL to a new file, stopping at `max_bytes`. The URL is never logged or put in an error."""
    try:
        with httpx.stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=False) as response:
            if response.status_code != 200:
                raise ProviderError(f"Earth Engine download failed with HTTP {response.status_code}")
            written = 0
            with open(destination, "xb") as out:
                for block in response.iter_bytes(1024 * 256):
                    written += len(block)
                    if written > max_bytes:
                        raise ProviderError(f"The download passed the {max_bytes:,}-byte limit and was stopped")
                    out.write(block)
    except httpx.HTTPError as exc:
        raise ProviderError(redact(f"Earth Engine download failed: {type(exc).__name__}")) from None


class EarthEngineProvider:
    name = "earthengine"
    synthetic = False

    def __init__(self, ee_module: Any, *, project: str, downloader: Downloader = http_download) -> None:
        self._ee = ee_module
        self.project = project
        self._download = downloader

    @classmethod
    def from_settings(
        cls, settings: Settings, *, ee_module: Any | None = None, downloader: Downloader | None = None
    ) -> "EarthEngineProvider":
        """The only way to get a real provider. Imports `ee` and contacts Google only after every check passes.

        GEE-1 closes this route: scene search with an area of interest (GEE-2) and export (GEE-4) are not enabled yet.
        After the global switches and the operating-model-A checks (control folder, kill switch, single-instance lock,
        usage log) it refuses before `ee` is imported, so this cannot reach Google. The class below is kept, and tested
        with a stand-in,
        for the phases that will use it."""
        ensure_enabled(settings)
        if not settings.earth_engine_project:
            raise ProviderUnavailable(
                "GEOPOC_EARTH_ENGINE_PROJECT is not set: Earth Engine needs a registered Cloud project"
            )
        from geopoc.earth_engine.errors import EarthEngineRefused  # local: that package imports this one's gate
        from geopoc.earth_engine.runtime import get_runtime

        get_runtime(settings).preflight("legacy-provider", require_developer=False)
        raise EarthEngineRefused(
            "EE_PHASE_NOT_ENABLED",
            "Scene search with an area of interest and exports are not enabled during GEE-1 (they are GEE-2 and "
            "GEE-4). "
            "Use `python -m geopoc.earth_engine verify` for the connection check. Nothing was sent.",
        )

    # --- search -----------------------------------------------------------------------------------------------------

    def search(self, query: SceneQuery) -> list[SceneInfo]:
        query.check()
        ee = self._ee
        collection = (
            ee.ImageCollection(COLLECTION_ID)
            .filterBounds(ee.Geometry(query.aoi.as_geojson()))
            .filterDate(query.dates.start.isoformat(), query.dates.end_exclusive.isoformat())
        )
        if query.max_cloud_percent is not None:
            collection = collection.filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", query.max_cloud_percent))
        try:
            listing = collection.sort("system:time_start").limit(query.limit).getInfo()
        except Exception as exc:
            raise ProviderError(redact(f"Earth Engine search failed: {type(exc).__name__}: {exc}")) from None
        scenes = []
        for feature in listing.get("features", []):
            props = feature.get("properties", {})
            millis = props.get("system:time_start")
            if millis is None or not feature.get("id"):
                continue  # a scene without an id or time cannot be recorded honestly
            cloud = props.get("CLOUDY_PIXEL_PERCENTAGE")
            scenes.append(
                SceneInfo(
                    scene_id=feature["id"],
                    collection=COLLECTION_ID,
                    acquisition_time=datetime.fromtimestamp(millis / 1000, tz=UTC),
                    cloud_percent=float(cloud) if cloud is not None else None,
                    tile=props.get("MGRS_TILE"),
                    processing_baseline=props.get("PROCESSING_BASELINE"),
                )
            )
        logger.info("earth engine search returned %d scene(s)", len(scenes))
        return scenes

    # --- export -----------------------------------------------------------------------------------------------------

    def export_geotiff(self, plan: ExportPlan, scene: SceneInfo, destination: Path) -> dict[str, Any]:
        ee = self._ee
        request = {
            "bands": list(plan.bands),
            "region": plan.aoi.bbox_geojson(),
            "scale": plan.scale_m,
            "crs": plan.crs,
            "format": "GEO_TIFF",
        }
        try:
            image = ee.Image(scene.scene_id).select(list(plan.bands))
            url = image.getDownloadURL(request)
        except Exception as exc:
            raise ProviderError(
                redact(f"Earth Engine could not prepare the export: {type(exc).__name__}: {exc}")
            ) from None
        self._download(url, destination, plan.limits.max_bytes)
        with open(destination, "rb") as handle:
            if handle.read(4) not in TIFF_MAGIC:
                # Earth Engine can answer with a zip for some requests; this prototype accepts only a plain GeoTIFF.
                raise ProviderError("Earth Engine returned something other than a GeoTIFF; nothing was kept")
        return {
            "request": {"method": "ee.Image.getDownloadURL", "image": scene.scene_id, **request},
            "attribution": ["Imagery retrieved through Google Earth Engine."],
            "service": {"endpoint": "earthengine.googleapis.com", "cloud_project": self.project},
        }
