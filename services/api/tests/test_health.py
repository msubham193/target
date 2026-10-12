from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from geopoc.api.main import app
from geopoc.config import get_settings
from geopoc.db.session import get_session


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["reachable"] is True
    assert body["database"]["postgis_version"].startswith("3.")
    assert body["database"]["schema_revision"] == "0002"
    assert body["versions"]["gdal"] and body["versions"]["rasterio"] and body["versions"]["pillow"]
    assert body["versions"]["ffprobe"] in {"available", "not found"}
    assert body["storage"]["data_root_ok"] is True
    assert body["storage"]["free_bytes"] > 0
    assert body["online"] == {"enabled": False, "proj_network": False}
    assert body["bind_host"] == "127.0.0.1"
    assert body["worker"]["implemented"] is False


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


def test_health_models_reports_empty_manifest_truthfully(client):
    response = client.get("/health/models")
    assert response.status_code == 200
    body = response.json()
    assert body["manifest_found"] is True
    assert body["models"] == []
    assert body["errors"] == []
    assert body["llm_runtime"]["configured"] is False
    assert body["llm_runtime"]["checked"] is False


def test_manifest_refuses_remote_models(tmp_path):
    from geopoc.registry.manifest import read_manifest

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "models:\n"
        "  - {name: local-one, version: '1', pipeline: ground_image, locality: local}\n"
        "  - {name: cloud-one, version: '1', pipeline: ground_image, locality: remote}\n",
        encoding="utf-8",
    )
    result = read_manifest(manifest)
    assert [m["name"] for m in result["models"]] == ["local-one"]
    assert result["models"][0]["available"] is False
    assert any("cloud-one" in error for error in result["errors"])


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
