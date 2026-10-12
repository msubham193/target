"""Tests run against a separate `geopoc_test` database in the project's PostGIS container."""

import os
from pathlib import Path

# Must be set before application settings are first read.
os.environ["GEOPOC_DB_NAME"] = "geopoc_test"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from geopoc.api.main import app
from geopoc.config import get_settings
from geopoc.db.session import get_engine

API_DIR = Path(__file__).resolve().parents[1]


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


@pytest.fixture(scope="session", autouse=True)
def database():
    assert get_settings().db_name == "geopoc_test"
    recreate_database("geopoc_test", drop=False)
    command.upgrade(alembic_config(get_settings().database_url()), "head")
    yield


def truncate_all() -> None:
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE audit_event, project, crs_definition, workspace_user CASCADE"))


@pytest.fixture(autouse=True)
def clean_tables(database):
    truncate_all()
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
