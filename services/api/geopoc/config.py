import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

REPO_ROOT = Path(__file__).resolve().parents[3]

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Scratch databases (tests, browser tests, smoke tests) are named geopoc_<something>. Anything else, including "geopoc"
# itself, is the working database. scripts/lib/common.ps1 and geopoc/smoke.py apply the same rule.
SCRATCH_DATABASE_PATTERN = re.compile(r"^geopoc_[a-z0-9_]+$")


def database_environment(name: str | None) -> str:
    """'scratch' for a database that may be reset freely, otherwise 'working'."""
    return "scratch" if name and SCRATCH_DATABASE_PATTERN.match(name) else "working"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEOPOC_", env_file=REPO_ROOT / ".env", extra="ignore")

    db_user: str = "geopoc"
    db_password: SecretStr = SecretStr("")
    db_name: str = "geopoc"
    db_host: str = "127.0.0.1"
    db_port: int = 5433
    data_root: Path = Path("D:/geopoc-data")
    # Scratch runs (end-to-end tests) set this to keep their files apart from the working data.
    data_subdir: str = ""
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    online_enabled: bool = False
    # Google Earth Engine (feasibility prototype, geopoc.satellite). Off by default, and never used unless BOTH this and
    # `online_enabled` are switched on by the account owner. Only the Cloud project id and the PATH of a credentials
    # file are
    # held here; no key, token or password is ever read into settings, logged or printed.
    earth_engine_enabled: bool = False
    earth_engine_project: str | None = None
    earth_engine_credentials_file: Path | None = None
    # GEE-1, operating model A (docs/earth-engine-integration-assessment.md, section 16.3a): a controlled evaluation by
    # named developers on one host. None of these holds a secret.
    # How the organisation-owned identity is obtained. `adc`, `wif` and `impersonation` all go through Application
    # Default Credentials (an attached, federated or impersonated service identity); `key_file` is the last resort and
    # also needs `earth_engine_allow_key_file`.
    earth_engine_auth_mode: Literal["adc", "wif", "impersonation", "key_file"] = "adc"
    earth_engine_allow_key_file: bool = False
    # A folder OUTSIDE the repository that holds the kill-switch flag, the single-instance lock and the append-only
    # usage log. Created only by `python -m geopoc.earth_engine init`. Earth Engine refuses to run without it (fail
    # closed).
    earth_engine_control_dir: Path | None = None
    # Comma-separated display names of the local profiles allowed to run Earth Engine operations. Empty means nobody.
    earth_engine_developers: str = ""
    # Fixed, low, server-side ceilings. They may be lowered here but never raised above the bounds in
    # geopoc.earth_engine.usage (a larger value makes Earth Engine refuse to run).
    earth_engine_max_requests_per_day: int = 40
    earth_engine_max_requests_per_operation: int = 3
    earth_engine_max_concurrent: int = 1
    # Satellite catalogue discovery (geopoc.imagery, docs/imagery-discovery.md). Off by default. A search sends a
    # coarse bounding box of the area of interest to a third-party catalogue, so it needs ALL of: `online_enabled`,
    # this switch, the account owner's confirmation of the provider's terms, a project set to `allow_online`, and an
    # explicit confirmation on each request. Nothing here is a secret except the optional token, which is never
    # returned or logged.
    imagery_enabled: bool = False
    imagery_terms_confirmed: bool = False
    imagery_cdse_stac_url: str = "https://stac.dataspace.copernicus.eu/v1"
    # Optional bearer token for the catalogue, obtained by the account owner outside this application. Sent only to the
    # configured catalogue address. Search is believed to need none (unverified).
    imagery_cdse_access_token: SecretStr | None = None
    imagery_timeout_s: float = 20.0
    imagery_max_response_bytes: int = 5 * 1024**2
    # The bounding box sent to the catalogue is rounded outward to this grid (degrees), so the exact area stays local.
    imagery_aoi_round_deg: float = 0.01
    # Derived spectral indices (geopoc.geo.indices): the one local folder searched for Sentinel-2 L2A band files and
    # their product metadata. Server side only; unset means no inputs are offered. Never supplied by a client.
    imagery_band_root: Path | None = None
    manifest_path: Path = REPO_ROOT / "models" / "manifest.yaml"
    # Folder that the manifest's weight paths are relative to. Defaults to the data root.
    weights_base: Path | None = None

    # Ingestion limits. These bound what is accepted; they do not promise a file is processable.
    max_ground_image_bytes: int = 50 * 1024**2
    max_raster_bytes: int = 1024**3
    max_video_bytes: int = 1024**3
    # Images are fully decoded to prove readability, so pixel count is capped to bound memory.
    max_image_pixels: int = 100_000_000
    # Phase 3b raster tools. Operating settings, not validated values.
    # Band statistics read every pixel up to this many per band; above it, an evenly spaced sample.
    statistics_exact_max_pixels: int = 16_000_000
    # A verified display copy with overviews is built for rasters larger than this on either side
    # (when the file has no overviews of its own).
    display_copy_min_side: int = 4096
    # Largest pixel comparison computed (pixels per band).
    compare_max_pixels: int = 400_000_000
    # Uploads are refused when they would leave less than this free on the data volume.
    storage_reserve_bytes: int = 1024**3
    media_tool_timeout_s: int = 60
    # 3D terrain view (docs/3d-terrain-viewer.md). A raster larger than this is refused; the grid sent to the viewer is
    # at most `terrain_max_grid_side` cells per side (default 256, ceiling 512), however large the raster is.
    terrain_max_source_pixels: int = 400_000_000
    terrain_max_grid_side: int = 512
    # Video-frame analysis (docs/video-frame-analysis.md). Ceilings a request may not exceed, not defaults to aim for.
    video_analysis_max_frames: int = 300
    video_analysis_min_interval_s: float = 0.2
    # Reading every packet's timestamp is a metadata scan (no decoding) but still reads the whole file.
    video_packet_scan_timeout_s: int = 600
    video_analysis_max_packets: int = 3_000_000

    # Background jobs.
    job_poll_interval_s: float = 1.0
    # A running job whose worker has not reported for this long is treated as lost and recovered.
    job_stale_after_s: float = 30.0
    job_cancel_grace_s: float = 5.0
    job_retry_backoff_s: float = 5.0
    # Stub models exist only for automated tests; they are refused unless this is switched on.
    allow_stub_models: bool = False

    @model_validator(mode="after")
    def _apply_data_subdir(self) -> "Settings":
        if self.data_subdir:
            self.data_root = self.data_root / self.data_subdir
        return self

    def database_url(self, database: str | None = None) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=database or self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
