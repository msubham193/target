"""Tests run against a separate `geopoc_test` database and a scratch data folder, never the working ones."""

import os
import shutil
import stat
from pathlib import Path

from geopoc.config import Settings

# Both must be set before application settings are first read.
os.environ["GEOPOC_DB_NAME"] = "geopoc_test"
TEST_DATA_ROOT = Settings().data_root / "_pytest"
os.environ["GEOPOC_DATA_ROOT"] = str(TEST_DATA_ROOT)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from geopoc.api.main import app  # noqa: E402
from geopoc.config import get_settings  # noqa: E402
from geopoc.db.session import get_engine  # noqa: E402
from geopoc.storage import store  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]
TABLES = "video_metadata, raster_metadata, asset, blob, audit_event, project, crs_definition, workspace_user"


def alembic_config(url) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["url"] = url
    return cfg


def recreate_database(name: str, *, drop: bool) -> None:
    admin = create_engine(get_settings().database_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if drop:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()


def drop_database(name: str) -> None:
    admin = create_engine(get_settings().database_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def wipe(path: Path) -> None:
    """Remove a scratch folder, including the read-only originals inside it."""

    def make_writable(function, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if path.exists():
        shutil.rmtree(path, onerror=make_writable)


@pytest.fixture(scope="session", autouse=True)
def database():
    assert get_settings().db_name == "geopoc_test"
    assert get_settings().data_root == TEST_DATA_ROOT
    recreate_database("geopoc_test", drop=False)
    command.upgrade(alembic_config(get_settings().database_url()), "head")
    yield
    wipe(TEST_DATA_ROOT)


def truncate_all() -> None:
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))


@pytest.fixture(autouse=True)
def clean_state(database):
    truncate_all()
    wipe(TEST_DATA_ROOT)
    store.init_storage()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def profile(client):
    response = client.post("/api/v1/workspace/profiles", json={"display_name": "Test Reviewer"})
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def headers(profile):
    return {"X-Workspace-Profile": profile["id"]}


@pytest.fixture
def project(client, headers):
    response = client.post("/api/v1/projects", json={"name": "Ingestion"}, headers=headers)
    assert response.status_code == 201
    return response.json()
