"""The PROJ environment: network off for both copies of PROJ, and whether they agree (CRS correctness hardening).

pyproj and rasterio each carry their own PROJ build and `proj.db`. These tests check that neither can go online, that the
two are described honestly (including what cannot be verified), that a disagreement in positions is a failure and not a
version warning, and that nothing else in the application can switch the network on.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pyproj
import pytest
import rasterio
import rasterio.env

from geopoc.geo import projenv

API = Path(__file__).resolve().parents[1]


@pytest.fixture
def fresh_report():
    """The report is cached for the life of the process; tests that change the world call the uncached function."""
    return projenv.report.__wrapped__


def codes(report):
    return {c["code"]: c["status"] for c in report["checks"]}


# --- network off ---------------------------------------------------------------------------------------------------------------


def test_network_is_off_for_pyproj_and_for_gdal():
    assert os.environ["PROJ_NETWORK"] == "OFF"
    assert pyproj.network.is_network_enabled() is False
    assert rasterio.env.get_gdal_config("PROJ_NETWORK") is False


def test_a_hostile_environment_cannot_switch_it_on():
    script = (
        "import os, pyproj, rasterio.env, geopoc;"
        "print(os.environ['PROJ_NETWORK'], pyproj.network.is_network_enabled(),"
        " rasterio.env.get_gdal_config('PROJ_NETWORK'), geopoc.PROJ_NETWORK_REQUESTED)"
    )
    for requested in ("ON", "on", "TRUE", "1"):
        out = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PROJ_NETWORK": requested},
            cwd=API,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.split() == ["OFF", "False", "False", requested], out.stdout


def test_a_request_to_go_online_is_reported_as_ignored(fresh_report, monkeypatch):
    import geopoc

    monkeypatch.setattr(geopoc, "PROJ_NETWORK_REQUESTED", "ON", raising=False)
    report = fresh_report()
    assert codes(report)["PROJ_NETWORK_REQUEST_IGNORED"] == "warning"
    assert report["network"]["requested_before_override"] == "ON" and report["network"]["pyproj_enabled"] is False


def test_nothing_else_in_the_application_sets_the_proj_network():
    allowed = {"__init__.py", "geo/projenv.py"}
    package = API / "geopoc"
    found = {
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if "PROJ_NETWORK" in path.read_text(encoding="utf-8") and "geo/crs.py" not in path.as_posix()
    }
    assert found <= allowed | {"api/routers/health.py"}, (
        f"another module touches PROJ_NETWORK: {sorted(found - allowed)}"
    )
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "set_network_enabled(True)" not in text and 'PROJ_NETWORK"] = "ON"' not in text, path


def test_gdal_is_never_configured_with_the_network_by_any_raster_environment():
    import re

    for path in (API / "geopoc").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"Env\([^)]*PROJ_NETWORK", text), f"{path} configures the PROJ network on a raster Env"


# --- what the report says -----------------------------------------------------------------------------------------------------


def test_the_report_names_both_copies_of_proj_and_their_databases(fresh_report):
    report = fresh_report()
    assert report["pyproj"]["proj_version"] == pyproj.proj_version_str
    assert report["gdal"]["proj_version"] == rasterio.__proj_version__ and report["gdal"]["gdal_version"]
    assert report["pyproj"]["data_dir"] != report["gdal"]["data_dir"]  # two separate databases
    for side in ("pyproj", "gdal"):
        assert report[side]["epsg_version"].startswith("v") and report[side]["layout"]
        assert report[side]["ini_network"].replace(" ", "") == "network=off"


def test_the_report_does_not_claim_to_have_verified_gdals_internal_state(fresh_report):
    report = fresh_report()
    assert report["network"]["gdal_internal_state_verified"] is False
    gdal = next(c for c in report["checks"] if c["code"] == "GDAL_NETWORK_CONFIG")
    assert gdal["status"] == "ok" and "not independently verified" in gdal["message"]


def test_an_unreadable_gdal_configuration_is_reported_as_not_verified(fresh_report, monkeypatch):
    def broken(_name):
        raise RuntimeError("no config")

    monkeypatch.setattr(rasterio.env, "get_gdal_config", broken)
    report = fresh_report()
    gdal = next(c for c in report["checks"] if c["code"] == "GDAL_NETWORK_CONFIG")
    assert gdal["status"] == "warning" and "not verified" in gdal["message"]
    assert report["network"]["gdal_configuration_enabled"] is None


def test_a_gdal_configuration_that_is_on_is_a_failure(fresh_report, monkeypatch):
    monkeypatch.setattr(rasterio.env, "get_gdal_config", lambda _name: True)
    report = fresh_report()
    assert codes(report)["GDAL_NETWORK_CONFIG"] == "failed" and report["status"] == "failed"


def test_a_proj_data_override_is_flagged(fresh_report, monkeypatch):
    monkeypatch.setenv("PROJ_DATA", "C:/somewhere/else")
    report = fresh_report()
    assert codes(report)["PROJ_DATA_OVERRIDE"] == "warning" and report["overrides"] == {
        "PROJ_DATA": "C:/somewhere/else"
    }
    monkeypatch.delenv("PROJ_DATA")
    monkeypatch.setenv("PROJ_LIB", "C:/old/way")
    assert fresh_report()["overrides"] == {"PROJ_LIB": "C:/old/way"}


# --- versions, and what a version difference does and does not mean ---------------------------------------------------------


def test_the_installed_proj_builds_share_a_release_line(fresh_report):
    report = fresh_report()
    assert codes(report)["PROJ_VERSION"] == "ok"
    assert projenv._major_minor(report["pyproj"]["proj_version"]) == projenv._major_minor(
        report["gdal"]["proj_version"]
    )  # noqa: SLF001


def test_a_different_minor_release_is_a_failure(fresh_report, monkeypatch):
    monkeypatch.setattr(rasterio, "__proj_version__", "9.4.0")
    report = fresh_report()
    assert codes(report)["PROJ_VERSION"] == "failed" and report["status"] == "failed"


def test_different_epsg_database_versions_are_a_warning_not_a_refusal(fresh_report):
    report = fresh_report()
    if report["pyproj"]["epsg_version"] == report["gdal"]["epsg_version"]:
        pytest.skip("the two installed databases carry the same EPSG version")
    assert codes(report)["EPSG_DATABASE_VERSION"] == "warning"
    assert report["status"] != "failed"  # a version difference alone never stops the application


def test_a_disagreement_in_positions_is_a_failure_and_is_not_hidden_by_the_epsg_warning(fresh_report, monkeypatch):
    monkeypatch.setattr(
        projenv,
        "parity",
        lambda: {
            "status": "failed",
            "tolerance_m": 0.001,
            "worst_difference_m": 3.2,
            "cases": [{"source": "a", "target": "b"}],
        },
    )
    report = fresh_report()
    assert codes(report)["PARITY"] == "failed" and report["status"] == "failed"
    parity = next(c for c in report["checks"] if c["code"] == "PARITY")
    assert "not a version warning" in parity["message"]


# --- the fixed-coordinate comparison ----------------------------------------------------------------------------------------


def test_the_two_engines_agree_on_every_fixed_pair():
    result = projenv.parity()
    assert result["status"] == "ok" and len(result["cases"]) == len(projenv.PARITY_CASES) >= 6
    assert result["worst_difference_m"] <= projenv.PARITY_TOLERANCE_M == 0.001
    assert {case["source"] for case in result["cases"]} >= {"EPSG:2046", "EPSG:2193", "EPSG:3031", "EPSG:32660"}


def test_the_comparison_catches_an_engine_that_disagrees(monkeypatch):
    import rasterio.warp

    real = rasterio.warp.transform
    monkeypatch.setattr(rasterio.warp, "transform", lambda s, t, xs, ys: real(s, t, [x + 1e-3 for x in xs], ys))
    assert projenv.parity()["status"] == "failed"


def test_the_comparison_catches_an_engine_that_cannot_convert(monkeypatch):
    import rasterio.warp

    def refuse(*_args):
        raise RuntimeError("no such operation")

    monkeypatch.setattr(rasterio.warp, "transform", refuse)
    result = projenv.parity()
    assert result["status"] == "failed" and "no such operation" in result["cases"][0]["error"]


# --- where it is reported -----------------------------------------------------------------------------------------------------


def test_health_reports_the_proj_environment(client):
    body = client.get("/health").json()
    proj = body["proj"]
    assert proj["status"] in ("ok", "warning") and proj["parity"]["status"] == "ok"
    assert proj["network"]["pyproj_enabled"] is False and proj["network"]["gdal_internal_state_verified"] is False
    assert body["online"]["proj_network"] is False


def test_a_problem_is_logged_at_startup_and_never_stops_the_api(monkeypatch, caplog):
    from geopoc.api import main

    monkeypatch.setattr(
        projenv,
        "report",
        lambda: {"status": "failed", "checks": [{"code": "PARITY", "status": "failed", "message": "positions differ"}]},
    )
    with caplog.at_level(logging.WARNING, logger="geopoc.api"):
        main._log_proj_environment()  # noqa: SLF001
    assert any("PARITY" in r.getMessage() and "positions differ" in r.getMessage() for r in caplog.records)

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(projenv, "report", broken)
    main._log_proj_environment()  # noqa: SLF001 - must not raise
