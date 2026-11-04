from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from geopoc.api.main import app
from geopoc.config import get_settings
from geopoc.db.session import get_session
from tests.conftest import alembic_config


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["reachable"] is True
    assert body["database"]["postgis_version"].startswith("3.")
    # the test database is migrated to the newest migration; the number is read from the files, not written here
    assert (
        body["database"]["schema_revision"]
        == ScriptDirectory.from_config(alembic_config(get_settings().database_url())).get_current_head()
    )
    assert body["versions"]["gdal"] and body["versions"]["rasterio"] and body["versions"]["pillow"]
    assert body["versions"]["ffprobe"] in {"available", "not found"}
    assert body["storage"]["data_root_ok"] is True
    assert body["storage"]["free_bytes"] > 0
    assert body["online"] == {"enabled": False, "proj_network": False}
    assert body["bind_host"] == "127.0.0.1"


def test_health_reports_unavailable_when_database_is_unreachable(client):
    """PT-05. The connection failure is simulated with an unused port; the container is not stopped."""
    url = get_settings().database_url().set(port=1)
    dead_engine = create_engine(url, connect_args={"connect_timeout": 1})

    def dead_session():
        with Session(dead_engine) as session:
            yield session

    app.dependency_overrides[get_session] = dead_session
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.clear()
        dead_engine.dispose()
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["database"]["reachable"] is False
    assert body["database"]["error_code"] == "DATABASE_UNAVAILABLE"


def test_health_models_lists_manifest_models_and_worker_state(client):
    response = client.get("/health/models")
    assert response.status_code == 200
    body = response.json()
    assert body["manifest_found"] is True
    stub = next(m for m in body["models"] if m["name"] == "stub-detector")
    assert stub["available"] is True and stub["score_calibrated"] is False
    assert body["worker"]["running"] is False  # no worker process is running during this test
    assert body["llm_runtime"]["configured"] is False
    assert body["llm_runtime"]["checked"] is False


def test_health_says_when_no_worker_is_running(client):
    worker = client.get("/health").json()["worker"]
    assert worker["running"] is False and "wait until one is started" in worker["note"]


def test_request_id_is_echoed_or_generated(client):
    assert client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"] == "abc-123"
    generated = client.get("/health", headers={"X-Request-ID": "bad id with spaces"}).headers["X-Request-ID"]
    assert generated != "bad id with spaces" and len(generated) == 32


def test_refuses_non_loopback_bind():
    import pytest

    from geopoc.__main__ import check_loopback

    check_loopback("127.0.0.1")
    with pytest.raises(SystemExit):
        check_loopback("0.0.0.0")
