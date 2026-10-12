from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
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
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    online_enabled: bool = False
    manifest_path: Path = REPO_ROOT / "models" / "manifest.yaml"

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
