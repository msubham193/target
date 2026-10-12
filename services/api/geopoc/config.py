from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

REPO_ROOT = Path(__file__).resolve().parents[3]

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


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
    manifest_path: Path = REPO_ROOT / "models" / "manifest.yaml"

    # Ingestion limits. These bound what is accepted; they do not promise a file is processable.
    max_ground_image_bytes: int = 50 * 1024**2
    max_raster_bytes: int = 1024**3
    max_video_bytes: int = 1024**3
    # Images are fully decoded to prove readability, so pixel count is capped to bound memory.
    max_image_pixels: int = 100_000_000
    # Uploads are refused when they would leave less than this free on the data volume.
    storage_reserve_bytes: int = 1024**3
    media_tool_timeout_s: int = 60

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
