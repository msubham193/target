"""What a satellite provider must do. The application depends on this, never on a particular service."""

from pathlib import Path
from typing import Any, Protocol

from geopoc.satellite.models import ExportPlan, SceneInfo, SceneQuery


class SatelliteProvider(Protocol):
    name: str
    #: True when the pixels are generated, not measured. A synthetic provider says so in every record it writes.
    synthetic: bool

    def search(self, query: SceneQuery) -> list[SceneInfo]:
        """Scenes over the area and dates, with their acquisition time and cloud metadata where the catalogue has it."""
        ...

    def export_geotiff(self, plan: ExportPlan, scene: SceneInfo, destination: Path) -> dict[str, Any]:
        """Write the GeoTIFF to `destination` (which must not exist) and return what was actually requested and
        credited:
        {"request": {...}, "attribution": [...], "service": {...}}. Must not log or return credentials or signed
        URLs."""
        ...
