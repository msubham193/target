"""Phase 4 follow-up: georeferencing recorded by a person for rasters with missing or invalid metadata.

All rasters here are synthetic GeoTIFFs written in memory with known coordinates, and every expected position is
computed independently with PyProj from the affine transform. This is synthetic validation only; no real-world
imagery is involved.
"""

import hashlib
import io
import math
import os
import stat

import numpy as np
import pyproj
import pytest
from PIL import Image
from rasterio.transform import Affine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from geopoc.db.session import get_engine
from geopoc.geo import tiles
from geopoc.storage import store
from tests import media
from tests.test_assets import upload

ORIGIN = 20037508.342789244
NORTH_UP = [500000.0, 10.0, 0.0, 3000000.0, 0.0, -10.0]  # GDAL order, EPSG:32643 on the 75 E meridian
TO_LONLAT = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)


def raster(client, project, headers, run_jobs, name="scan.tif", **kwargs):
    body = media.geotiff_bytes(**kwargs)
    response = upload(client, project, headers, name, body, modality="overhead_raster")
    assert response.status_code == 201, response.text
    run_jobs()  # preview
    return response.json()["asset"], body


def state(client, asset):
    response = client.get(f"/api/v1/assets/{asset['id']}/georeferencing")
    assert response.status_code == 200, response.text
    return response.json()


def validate(client, asset, **body):
    response = client.post(f"/api/v1/assets/{asset['id']}/georeferencing/validate", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def record(client, headers, asset, expected_version=0, confirm=True, **body):
    return client.post(
        f"/api/v1/assets/{asset['id']}/georeferencing",
        json={"expected_version": expected_version, "confirm": confirm, **body},
        headers=headers,
    )


def checks(result) -> dict[str, str]:
    return {c["code"]: c["status"] for c in result["checks"]}


def layers(client, project):
    return client.get(f"/api/v1/projects/{project['id']}/layers").json()


def tile_of(lon, lat, z):
    x_m, y_m = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(lon, lat)
    size = 2 * ORIGIN / 2**z
    fx, fy = (x_m + ORIGIN) / size, (ORIGIN - y_m) / size
    return int(fx), int(fy), (fx - int(fx)) * 256, (fy - int(fy)) * 256


def alpha_at(client, asset, lon, lat, z=15, version=None):
    x, y, px, py = tile_of(lon, lat, z)
    url = f"/api/v1/assets/{asset['id']}/tiles/{z}/{x}/{y}.png" + (f"?v={version}" if version is not None else "")
    response = client.get(url)
    if response.status_code == 204:
        return 0
    assert response.status_code == 200, response.text
    return int(np.asarray(Image.open(io.BytesIO(response.content)))[int(py), int(px), 3])


def audit_actions():
    with get_engine().connect() as conn:
        return (
            conn.execute(text("SELECT action FROM audit_event WHERE action LIKE 'georeference.%' ORDER BY at"))
            .scalars()
            .all()
        )


REASON = "CRS stated in the supplier's delivery note for this scene."


# --- case A: coordinates exist, CRS missing --------------------------------------------------------------------------------


def test_assigning_a_crs_to_existing_coordinates_places_the_raster(client, project, headers, run_jobs, profile):
    asset, body = raster(client, project, headers, run_jobs, crs=None, size=(200, 150))
    before = state(client, asset)
    assert before["current"]["georeferenced"] is False and before["current"]["version"] == 0
    assert "declares no CRS" in before["unplaced_reason"]
    assert before["options"]["assign_crs"] is True
    assert before["file"]["geotransform"] == NORTH_UP and before["file"]["crs_status"] == "missing"
    assert [e["reason"] for e in layers(client, project)["excluded"]] == ["CRS_MISSING"]
    assert layers(client, project)["excluded"][0]["georeferencing_can_be_recorded"] is True

    proposal = validate(client, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON)
    assert proposal["valid"] is True, proposal["checks"]
    found = checks(proposal)
    for code in ("REASON_RECORDED", "CRS_RESOLVES", "GEOTRANSFORM_AVAILABLE", "COORDINATES_FIT_CRS", "TILE_RENDERS"):
        assert found[code] == "ok"
    assert found["WITHIN_AREA_OF_USE"] == "ok"
    # Pixel-to-coordinate mapping, checked independently: the top-left corner is the geotransform origin.
    corner = next(c for c in proposal["corners"] if c["point"] == "top-left corner")
    assert corner["native"] == [500000.0, 3000000.0]
    assert corner["lon_lat"] == pytest.approx(list(TO_LONLAT.transform(500000, 3000000)), abs=1e-9)
    bottom_right = next(c for c in proposal["corners"] if c["point"] == "bottom-right corner")
    assert bottom_right["native"] == [502000.0, 2998500.0]
    assert "kept exactly" in proposal["explanation"]
    assert state(client, asset)["current"]["version"] == 0  # validating stores nothing

    unconfirmed = record(
        client, headers, asset, confirm=False, operation="assign_crs", crs_id="EPSG:32643", reason=REASON
    )
    assert unconfirmed.status_code == 422 and unconfirmed.json()["code"] == "CONFIRMATION_REQUIRED"
    no_reason = record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32643", reason="  ")
    assert no_reason.status_code == 422 and no_reason.json()["code"] == "GEOREFERENCE_INVALID"
    assert [e["field"] for e in no_reason.json()["errors"]] == ["REASON_RECORDED"]

    done = record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON)
    assert done.status_code == 201, done.text
    after = done.json()
    assert after["current"]["georeferenced"] is True and after["current"]["source"] == "recorded"
    assert after["current"]["version"] == 1 and after["current"]["crs_id"] == "EPSG:32643"
    assert after["current"]["geotransform"] == NORTH_UP  # the coordinates were not changed
    assert after["unplaced_reason"] is None
    (version,) = after["history"]
    assert version["operation"] == "assign_crs" and version["method"] == "registry_crs_selected"
    assert version["reason"] == REASON and version["actor_label"] == profile["display_name"]
    assert version["previous"]["georeferenced"] is False and version["previous"]["crs_id"] is None
    assert version["result"]["crs_id"] == "EPSG:32643"
    assert version["source_checksum"] == hashlib.sha256(body).hexdigest()
    assert {c["code"] for c in version["validation"]["checks"]} >= {"ORIGINAL_UNCHANGED", "TILE_RENDERS"}
    assert version["validation"]["derived_artifacts"] == []
    # What the file declares is unchanged; the asset reports the recorded version separately.
    detail = client.get(f"/api/v1/assets/{asset['id']}").json()
    assert detail["raster"]["crs_status"] == "missing" and detail["raster"]["georeferenced"] is False
    assert detail["georeferencing"] == {
        "version": 1,
        "source": "recorded",
        "placed": True,
        "crs_id": "EPSG:32643",
        "crs_name": "WGS 84 / UTM zone 43N",
        "unplaced_reason": None,
    }
    assert audit_actions() == ["georeference.refused", "georeference.assign_crs"]

    (layer,) = layers(client, project)["rasters"]
    assert layer["placement"] == "user_asserted" and layer["georef_version"] == 1
    assert layer["tile_url"].endswith("?v=1")
    assert layer["bounds_4326"][0] == pytest.approx(75.0, abs=1e-6)
    centre = TO_LONLAT.transform(501000, 2999250)
    assert alpha_at(client, asset, *centre, version=1) == 255
    assert client.get(f"/api/v1/assets/{asset['id']}/content").content == body  # original bytes unchanged


def test_a_raster_whose_own_georeferencing_is_valid_is_not_overridden(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, name="good.tif")
    assert state(client, asset)["options"]["assign_crs"] is False
    refused = record(client, headers, asset, operation="assign_crs", crs_id="EPSG:4326", reason=REASON)
    assert refused.status_code == 409 and refused.json()["code"] == "ALREADY_GEOREFERENCED"
    preview = client.post(
        f"/api/v1/assets/{asset['id']}/georeferencing/validate", json={"operation": "assign_crs", "crs_id": "EPSG:4326"}
    )
    assert preview.status_code == 409


# --- case B: CRS known, no geotransform ------------------------------------------------------------------------------------


def test_a_crs_alone_never_makes_a_raster_without_a_geotransform_mappable(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, name="nogt.tif", transform=None)
    before = state(client, asset)
    assert before["file"]["crs_status"] == "valid" and before["file"]["geotransform"] is None
    assert before["options"]["assign_crs"] is False and "cannot place it" in before["options"]["assign_crs_reason"]
    assert "no usable geotransform" in before["unplaced_reason"]

    attempt = validate(client, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON)
    assert attempt["valid"] is False and checks(attempt)["GEOTRANSFORM_AVAILABLE"] == "failed"
    assert record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON).status_code == 422
    assert state(client, asset)["history"] == []
    assert [e["reason"] for e in layers(client, project)["excluded"]] == ["NO_GEOTRANSFORM"]

    # An explicit geotransform from a documented source is a separate, supported method.
    without_source = validate(client, asset, operation="set_geotransform", geotransform=NORTH_UP, reason=REASON)
    assert checks(without_source)["SOURCE_RECORDED"] == "failed"
    entered = record(
        client,
        headers,
        asset,
        operation="set_geotransform",
        geotransform=NORTH_UP,
        geotransform_source="Supplier metadata sheet, scene 17, 'Upper left corner' values",
        reason=REASON,
    )
    assert entered.status_code == 201, entered.text
    current = entered.json()["current"]
    assert current["georeferenced"] and current["crs_id"] == "EPSG:32643"  # the file's own CRS is kept
    assert entered.json()["history"][0]["method"] == "geotransform_entered"
    assert layers(client, project)["rasters"][0]["placement"] == "user_asserted"


def test_a_geotransform_needs_a_crs_when_none_is_usable(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, name="bare.tif", crs=None, transform=None)
    assert state(client, asset)["options"]["crs_required_with_geotransform"] is True
    source = "Values from the provider's world file"
    no_crs = validate(
        client, asset, operation="set_geotransform", geotransform=NORTH_UP, geotransform_source=source, reason=REASON
    )
    assert checks(no_crs)["CRS_RESOLVES"] == "failed" and no_crs["valid"] is False
    with_crs = record(
        client, headers, asset, operation="set_geotransform", geotransform=NORTH_UP, crs_id="epsg:32643",
        geotransform_source=source, reason=REASON,
    )  # fmt: skip
    assert with_crs.status_code == 201 and with_crs.json()["current"]["crs_id"] == "EPSG:32643"


# --- invalid input ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("crs_id", "expected"),
    [
        ("WGS84 + LCC", "complete CRS"),
        ("EPSG:999999", "not in the local CRS registry"),
        ("EPSG:5773", "not a 2D geographic or projected CRS"),  # a vertical CRS
    ],
)
def test_unresolvable_or_unsuitable_crs_is_refused(client, project, headers, run_jobs, crs_id, expected):
    asset, _ = raster(client, project, headers, run_jobs, crs=None)
    result = validate(client, asset, operation="assign_crs", crs_id=crs_id, reason=REASON)
    assert result["valid"] is False
    failed = next(c for c in result["checks"] if c["code"] == "CRS_RESOLVES")
    assert failed["status"] == "failed" and expected in failed["message"]


@pytest.mark.parametrize(
    ("geotransform", "expected"),
    [
        ([0, 1, 0, 0, 0, 1], "identity"),
        ([500000, 0, 0, 3000000, 0, 0], "degenerate"),
        ([500000, 10, 10, 3000000, 10, 10], "degenerate"),
    ],
)
def test_malformed_geotransforms_are_refused(client, project, headers, run_jobs, geotransform, expected):
    asset, _ = raster(client, project, headers, run_jobs, crs=None, transform=None)
    result = validate(
        client, asset, operation="set_geotransform", geotransform=geotransform, crs_id="EPSG:32643",
        geotransform_source="test", reason=REASON,
    )  # fmt: skip
    failed = next(c for c in result["checks"] if c["code"] == "GEOTRANSFORM_VALID")
    assert failed["status"] == "failed" and expected in failed["message"]
    short = client.post(
        f"/api/v1/assets/{asset['id']}/georeferencing/validate",
        json={"operation": "set_geotransform", "geotransform": [1, 2, 3]},
    )
    assert short.status_code == 422


def test_a_world_file_is_read_with_its_pixel_centre_convention(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, crs=None, transform=None)
    # A world file gives the centre of the top-left pixel: (500005, 2999995) for a 10 m grid starting at 500000.
    world = "10.0\n0.0\n0.0\n-10.0\n500005.0\n2999995.0\n"
    result = validate(
        client, asset, operation="set_geotransform", world_file=world, crs_id="EPSG:32643",
        geotransform_source="scene.tfw from the delivery", reason=REASON,
    )  # fmt: skip
    assert result["valid"] is True, result["checks"]
    assert result["proposed"]["geotransform"] == NORTH_UP  # converted to GDAL's top-left corner
    assert result["method"] == "world_file_entered"
    broken = validate(
        client, asset, operation="set_geotransform", world_file="10\n0\nabc\n", crs_id="EPSG:32643",
        geotransform_source="x", reason=REASON,
    )  # fmt: skip
    assert checks(broken)["GEOTRANSFORM_VALID"] == "failed"


# --- case C: invalid extents and failed transformations --------------------------------------------------------------------


def test_coordinates_that_do_not_fit_the_crs_are_refused_and_nothing_is_stored(client, project, headers, run_jobs):
    """UTM-style metres given a geographic CRS would put the raster at latitude 3 000 000: refused."""
    asset, _ = raster(client, project, headers, run_jobs, crs=None)
    result = validate(client, asset, operation="assign_crs", crs_id="EPSG:4326", reason=REASON)
    assert result["valid"] is False and result["proposed"] is None
    assert checks(result)["COORDINATES_FIT_CRS"] == "failed"
    assert checks(result)["UNITS_PLAUSIBLE"] == "warning"
    refused = record(client, headers, asset, operation="assign_crs", crs_id="EPSG:4326", reason=REASON)
    assert refused.status_code == 422
    assert "COORDINATES_FIT_CRS" in [e["field"] for e in refused.json()["errors"]]
    assert state(client, asset)["current"]["version"] == 0 and state(client, asset)["history"] == []
    assert layers(client, project)["rasters"] == []
    assert audit_actions() == ["georeference.refused"]


def test_implausible_units_and_area_of_use_are_warned_not_hidden(client, project, headers, run_jobs):
    # Degrees given to a projected CRS: valid numbers, tiny pixels, far from the CRS's area. Warned, not guessed.
    degrees = [75.0, 0.0001, 0.0, 27.0, 0.0, -0.0001]
    asset, _ = raster(client, project, headers, run_jobs, crs=None, transform=Affine.from_gdal(*degrees))
    result = validate(client, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON)
    found = checks(result)
    assert found["UNITS_PLAUSIBLE"] == "warning"
    assert found["WITHIN_AREA_OF_USE"] == "warning"
    assert result["valid"] is True, result["checks"]  # warnings are shown for review; they are not errors


# --- rotated rasters -----------------------------------------------------------------------------------------------------


def test_a_rotated_geotransform_is_used_in_full(client, project, headers, run_jobs):
    angle = math.radians(30)
    rotated = Affine(
        10 * math.cos(angle), -10 * math.sin(angle), 500000, -10 * math.sin(angle), -10 * math.cos(angle), 3000000
    )
    asset, _ = raster(client, project, headers, run_jobs, crs=None, transform=rotated, size=(200, 200))
    result = validate(client, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON)
    assert result["valid"] is True and checks(result)["GEOTRANSFORM_VALID"] == "warning"  # rotated, and said so
    for corner in result["corners"]:
        x, y = rotated @ tuple(corner["pixel"])
        assert corner["native"] == pytest.approx([x, y])
        assert corner["lon_lat"] == pytest.approx(list(TO_LONLAT.transform(x, y)), abs=1e-9)
    assert record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON).status_code == 201
    inside = TO_LONLAT.transform(*(rotated @ (100, 100)))
    xs, ys = zip(*[rotated @ c for c in ((0, 0), (200, 0), (200, 200), (0, 200))], strict=True)
    outside = TO_LONLAT.transform(min(xs) + 30, max(ys) - 30)  # inside the bounding box, outside the raster
    assert alpha_at(client, asset, *inside) == 255
    assert alpha_at(client, asset, *outside) == 0


# --- versions, concurrency, revert and tiles -------------------------------------------------------------------------------


def test_concurrent_changes_are_refused_instead_of_overwritten(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, crs=None)
    assert record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON).status_code == 201
    late = record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32644", reason="Another reviewer")
    assert late.status_code == 409 and late.json()["code"] == "GEOREFERENCE_VERSION_CONFLICT"
    assert "version 1" in late.json()["detail"]
    assert state(client, asset)["current"]["crs_id"] == "EPSG:32643"


def test_a_new_version_replaces_cached_tiles_and_revert_restores_the_file_state(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, crs=None, size=(200, 150))
    record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON)
    point = TO_LONLAT.transform(500500, 2999500)  # inside version 1's footprint, near its north-west corner
    assert alpha_at(client, asset, *point, version=1) == 255

    shifted = [503000.0, 10.0, 0.0, 3000000.0, 0.0, -10.0]  # 3 km east: the point is now outside the raster
    moved = record(
        client, headers, asset, expected_version=1, operation="set_geotransform", geotransform=shifted,
        geotransform_source="Corrected origin from the supplier's errata", reason="Supplier corrected the origin.",
    )  # fmt: skip
    assert moved.status_code == 201
    assert moved.json()["history"][1]["previous"]["geotransform"] == NORTH_UP
    # The same tile address, asked again: rendered from the new version, not served from the cache.
    assert alpha_at(client, asset, *point) == 0
    assert alpha_at(client, asset, *TO_LONLAT.transform(503500, 2999500), version=2) == 255
    assert layers(client, project)["rasters"][0]["tile_url"].endswith("?v=2")

    back = record(client, headers, asset, expected_version=2, operation="revert_to_file", reason="Back to the file.")
    assert back.status_code == 201
    current = back.json()["current"]
    assert (current["version"], current["source"], current["georeferenced"]) == (3, "file", False)
    assert [v["operation"] for v in back.json()["history"]] == ["assign_crs", "set_geotransform", "revert_to_file"]
    refused = client.get(f"/api/v1/assets/{asset['id']}/tiles/15/0/0.png")
    assert refused.status_code == 409 and refused.json()["code"] == "CRS_UNRESOLVED"
    assert [e["reason"] for e in layers(client, project)["excluded"]] == ["CRS_MISSING"]


def test_a_failed_tile_check_keeps_the_previous_valid_state(client, project, headers, run_jobs, monkeypatch):
    asset, _ = raster(client, project, headers, run_jobs, crs=None, size=(200, 150))
    record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON)

    def broken(*args, **kwargs):
        raise RuntimeError("simulated GDAL failure")

    monkeypatch.setattr(tiles, "render_tile", broken)
    attempt = record(
        client, headers, asset, expected_version=1, operation="set_geotransform",
        geotransform=[503000.0, 10.0, 0.0, 3000000.0, 0.0, -10.0], geotransform_source="test", reason=REASON,
    )  # fmt: skip
    assert attempt.status_code == 422
    failed = [e for e in attempt.json()["errors"] if e["field"] == "TILE_RENDERS"]
    assert failed and "simulated GDAL failure" in failed[0]["message"]
    monkeypatch.undo()
    current = state(client, asset)["current"]
    assert (current["version"], current["georeferenced"], current["geotransform"]) == (1, True, NORTH_UP)
    assert alpha_at(client, asset, *TO_LONLAT.transform(500500, 2999500), version=1) == 255


def test_a_changed_original_is_detected_and_nothing_is_recorded(client, project, headers, run_jobs):
    asset, body = raster(client, project, headers, run_jobs, crs=None)
    sha = hashlib.sha256(body).hexdigest()
    path = next(p for p in store.absolute_path("originals").rglob(sha))
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    try:
        path.write_bytes(body + b"tampered")
        refused = record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON)
        assert refused.status_code == 409 and refused.json()["code"] == "ORIGINAL_CHANGED"
    finally:
        path.write_bytes(body)
        os.chmod(path, stat.S_IREAD)
    assert state(client, asset)["history"] == []


def test_the_file_metadata_and_the_history_cannot_be_rewritten(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, crs=None)
    record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON)
    for statement in (
        "UPDATE raster_metadata SET crs_status = 'valid' WHERE asset_id = :id",
        "UPDATE raster_metadata SET geotransform = NULL WHERE asset_id = :id",
        "UPDATE georef_assertion SET reason = 'rewritten' WHERE asset_id = :id",
        "DELETE FROM georef_assertion WHERE asset_id = :id",
    ):
        with pytest.raises(DBAPIError, match="cannot be changed|append-only"):
            with get_engine().begin() as conn:
                conn.execute(text(statement), {"id": asset["id"]})


def test_only_rasters_with_a_profile_can_be_georeferenced(client, project, headers, run_jobs):
    photo = upload(client, project, headers, "photo.jpg", media.image_bytes("JPEG")).json()["asset"]
    assert client.get(f"/api/v1/assets/{photo['id']}/georeferencing").json()["code"] == "NOT_A_RASTER"
    asset, _ = raster(client, project, headers, run_jobs, crs=None)
    anonymous = client.post(
        f"/api/v1/assets/{asset['id']}/georeferencing",
        json={
            "operation": "assign_crs",
            "crs_id": "EPSG:32643",
            "reason": REASON,
            "expected_version": 0,
            "confirm": True,
        },
    )
    assert anonymous.status_code == 401
    assert client.get("/api/v1/assets/00000000-0000-4000-8000-000000000000/georeferencing").status_code == 404
