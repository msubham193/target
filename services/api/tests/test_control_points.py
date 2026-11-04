"""Georeferencing a scanned map sheet from control points supplied by a person.

The "truth" in each test is a known affine map worked out here, from which control points are generated; the application
must recover it, report residuals honestly, and refuse bad input. Synthetic only: nothing here shows that real sheets
(printed in their own projections) fit a single affine map.
"""

import math

import pytest
from pyproj import Transformer

from geopoc.geo import controlpoints
from geopoc.geo.controlpoints import Point
from tests import media
from tests.test_assets import rows, stored_files
from tests.test_georeferencing import TO_LONLAT, alpha_at
from tests.test_map_sheets import layers, put

WIDTH, HEIGHT = 400, 300
UTM = "EPSG:32643"
TRUE_GT = [500000.0, 10.0, 0.0, 3000000.0, 0.0, -10.0]  # 10 m pixels, north-up
GRID = [(0, 0), (400, 0), (400, 300), (0, 300), (200, 150)]


def ground(gt, column, row):
    return gt[0] + column * gt[1] + row * gt[2], gt[3] + column * gt[4] + row * gt[5]


def points(gt=None, cells=GRID, nudge=None):
    gt = gt or TRUE_GT
    out = []
    for index, (c, r) in enumerate(cells):
        x, y = ground(gt, c, r)
        dx, dy = (nudge or {}).get(index, (0.0, 0.0))
        out.append({"label": f"P{index + 1}", "column": c, "row": r, "x": x + dx, "y": y + dy})
    return out


def body(pts, crs=UTM, **extra):
    return {
        "crs_id": crs,
        "points": pts,
        "source": "Printed 1 km grid ticks read off the sheet by the reviewer.",
        "reason": "No georeferencing in the scan; control points entered from the printed grid.",
        **extra,
    }


def scan(client, project, headers, run_jobs, name="scan.png", series="osm"):
    data = media.image_bytes("PNG", size=(WIDTH, HEIGHT))
    return put(client, project, headers, run_jobs, name, data, series=series)["asset"], data


def validate(client, asset, payload):
    response = client.post(f"/api/v1/assets/{asset['id']}/control-points/validate", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def codes(result):
    return {c["code"]: c["status"] for c in result["checks"]}


# --- the fit itself ---------------------------------------------------------------------------------------------------


def fit(gt, cells=GRID, crs="EPSG:32643", nudge=None):
    import pyproj

    pts = [Point(p["label"], p["column"], p["row"], p["x"], p["y"]) for p in points(gt, cells, nudge)]
    return controlpoints.fit(pts, WIDTH, HEIGHT, pyproj.CRS(crs))


def test_a_known_north_up_map_is_recovered_exactly():
    result = fit(TRUE_GT)
    assert result.geotransform == pytest.approx(TRUE_GT, abs=1e-6)
    assert result.rms_pixels < 1e-9 and result.redundant is True and result.ok
    # Rotation is measured on the ground by finite differences, so "none" is zero to about 1e-7 degrees, not exactly.
    assert abs(result.rotation_degrees) < 1e-6


def test_a_rotated_and_scaled_map_is_recovered_and_the_rotation_is_reported():
    theta = math.radians(12)
    gt = [501234.5, 8 * math.cos(theta), 8 * math.sin(theta), 2999000.25, 8 * math.sin(theta), -8 * math.cos(theta)]
    result = fit(gt)
    assert result.geotransform == pytest.approx(gt, abs=1e-6)
    assert result.rotation_degrees == pytest.approx(12.0, abs=1e-6)
    assert result.pixel_size == pytest.approx([8.0, 8.0], abs=1e-9)
    assert {c.code: c.status for c in result.checks}["CONTROL_POINTS_ROTATION"] == "warning"


def test_three_points_fit_exactly_but_are_flagged_as_unverifiable():
    result = fit(TRUE_GT, cells=[(0, 0), (400, 0), (0, 300)])
    assert result.geotransform == pytest.approx(TRUE_GT, abs=1e-6) and result.redundant is False
    # Renamed from CONTROL_POINTS_REDUNDANCY: the warning is permanent and says the zero residual means nothing.
    assert {c.code: c.status for c in result.checks}["EXACT_FIT_NO_CHECK"] == "warning"


def test_a_misplaced_point_shows_in_the_residuals():
    result = fit(TRUE_GT, cells=[*GRID, (100, 50), (300, 250)], nudge={5: (80.0, 0.0)})  # 8 pixels off
    assert result.max_pixels > 4
    status = {c.code: c.status for c in result.checks}["CONTROL_POINTS_RESIDUALS"]
    assert status in ("warning", "failed")
    worst = max(result.residuals, key=lambda r: r["residual_pixels"])
    assert worst["label"] == "P6"


def test_a_grossly_wrong_point_makes_the_fit_fail():
    result = fit(TRUE_GT, nudge={4: (900.0, -700.0)})
    assert result.ok is False
    assert {c.code: c.status for c in result.checks}["CONTROL_POINTS_RESIDUALS"] == "failed"


def test_points_on_one_line_cannot_fix_a_map():
    result = fit(TRUE_GT, cells=[(0, 0), (100, 0), (200, 0), (300, 0)])
    assert result.ok is False and not result.geotransform
    assert {c.code: c.status for c in result.checks}["CONTROL_POINTS_SPREAD"] == "failed"


def test_clustered_points_are_flagged_as_extrapolating():
    result = fit(TRUE_GT, cells=[(10, 10), (60, 10), (60, 60), (10, 60)])
    assert result.ok is True
    assert {c.code: c.status for c in result.checks}["CONTROL_POINTS_SPREAD"] == "warning"


def test_unusable_points_are_refused():
    outside = fit(TRUE_GT, cells=[(0, 0), (401, 0), (400, 300), (0, 300)])
    assert not outside.geotransform and "CONTROL_POINTS_INSIDE_IMAGE" in {
        c.code for c in outside.checks if c.status == "failed"
    }
    duplicate = fit(TRUE_GT, cells=[(0, 0), (0, 0), (400, 300), (0, 300)])
    assert not duplicate.geotransform and "CONTROL_POINTS_DISTINCT" in {
        c.code for c in duplicate.checks if c.status == "failed"
    }
    nan = controlpoints.fit(
        [Point("a", 0, 0, float("nan"), 1), Point("b", 1, 0, 2, 3), Point("c", 0, 1, 4, 5)],
        10,
        10,
        __import__("pyproj").CRS("EPSG:32643"),
    )
    assert not nan.geotransform and "CONTROL_POINTS_FINITE" in {c.code for c in nan.checks if c.status == "failed"}


def test_a_mirrored_picture_is_flagged():
    mirrored = [500000.0, 10.0, 0.0, 2997000.0, 0.0, 10.0]  # rows run north
    result = fit(mirrored)
    assert {c.code: c.status for c in result.checks}["CONTROL_POINTS_ORIENTATION"] == "warning"


def test_geographic_control_points_do_not_count_normal_degree_distortion_as_a_bad_scale():
    # 0.001 deg of longitude is shorter on the ground than 0.001 deg of latitude, so equal degrees per pixel is not
    # an error for the pixel shape the scan has.
    gt = [77.0, 0.0001, 0.0, 28.1, 0.0, -0.00009]
    pts = [Point(f"P{i}", c, r, *ground(gt, c, r)) for i, (c, r) in enumerate(GRID)]
    import pyproj

    result = controlpoints.fit(pts, WIDTH, HEIGHT, pyproj.CRS("EPSG:4326"))
    assert result.ok and result.geotransform == pytest.approx(gt, abs=1e-12)
    assert result.rms_m is not None and result.rms_m < 1e-6


# --- through the API --------------------------------------------------------------------------------------------------


def test_control_points_place_a_scanned_sheet_and_record_their_evidence(client, project, headers, run_jobs, profile):
    asset, data = scan(client, project, headers, run_jobs)
    payload = body(points())
    result = validate(client, asset, payload)
    assert result["valid"] is True, result["checks"]
    assert result["fit"]["geotransform"] == pytest.approx(TRUE_GT, abs=1e-6) and result["fit"]["redundant"] is True
    assert result["fit"]["rms_pixels"] < 1e-9 and len(result["fit"]["residuals"]) == 5
    found = codes(result)
    assert found["CONTROL_POINTS_RESIDUALS"] == "ok" and found["COORDINATES_FIT_CRS"] == "ok"
    assert found["TILE_RENDERS"] == "ok"
    # Validating stored nothing.
    assert rows("SELECT count(*) FROM georef_assertion")[0][0] == 0

    unconfirmed = client.post(
        f"/api/v1/assets/{asset['id']}/control-points", json={**payload, "expected_version": 0}, headers=headers
    )
    assert unconfirmed.status_code == 422 and unconfirmed.json()["code"] == "CONFIRMATION_REQUIRED"

    done = client.post(
        f"/api/v1/assets/{asset['id']}/control-points",
        json={**payload, "expected_version": 0, "confirm": True},
        headers=headers,
    )
    assert done.status_code == 201, done.text
    state = done.json()
    assert state["current"]["georeferenced"] is True and state["current"]["source"] == "recorded"
    assert state["current"]["crs_id"] == UTM and state["current"]["geotransform"] == pytest.approx(TRUE_GT, abs=1e-6)
    (version,) = state["history"]
    assert version["operation"] == "set_geotransform" and version["actor_label"] == profile["display_name"]
    assert (
        "5 control points" in version["geotransform_source"]
        and "Printed 1 km grid ticks" in version["geotransform_source"]
    )
    evidence = version["validation"]["control_points"]
    assert evidence["method"] == "affine_least_squares" and evidence["point_count"] == 5
    assert [p["label"] for p in evidence["points"]] == ["P1", "P2", "P3", "P4", "P5"]
    assert evidence["rms_pixels"] < 1e-9 and evidence["rms_m"] < 1e-6 and evidence["source"].startswith("Printed")

    info = client.get(f"/api/v1/assets/{asset['id']}").json()["map_sheet"]
    assert info["placed"] is True and info["georeferencing_basis"] == "control_points"
    (layer,) = layers(client, project)["rasters"]
    assert layer["placement"] == "user_asserted" and layer["map_sheet"]["georeferencing_basis"] == "control_points"
    lon, lat = TO_LONLAT.transform(502000, 2998500)  # the picture's centre
    assert alpha_at(client, asset, lon, lat, z=15, version=1) > 0
    outside_lon, outside_lat = TO_LONLAT.transform(520000, 2998500)
    assert alpha_at(client, asset, outside_lon, outside_lat, z=15, version=1) == 0

    # The original file and its own recorded metadata are untouched; the history and audit record the change.
    assert [p.read_bytes() for p in stored_files() if p.name == asset["sha256"]] == [data]
    assert client.get(f"/api/v1/assets/{asset['id']}").json()["raster"]["crs_status"] == "missing"
    (audit,) = [r[0] for r in rows("SELECT details FROM audit_event WHERE action = 'georeference.control_points'")]
    assert audit["points"] == 5 and audit["rms_pixels"] == pytest.approx(0, abs=1e-6)


def test_a_poor_fit_is_refused_and_nothing_is_recorded(client, project, headers, run_jobs):
    asset, _ = scan(client, project, headers, run_jobs)
    payload = body(points(nudge={4: (900.0, -700.0)}))
    assert validate(client, asset, payload)["valid"] is False
    refused = client.post(
        f"/api/v1/assets/{asset['id']}/control-points",
        json={**payload, "expected_version": 0, "confirm": True},
        headers=headers,
    )
    assert refused.status_code == 422 and refused.json()["code"] == "CONTROL_POINTS_INVALID"
    assert "CONTROL_POINTS_RESIDUALS" in {e["field"] for e in refused.json()["errors"]}
    assert rows("SELECT count(*) FROM georef_assertion")[0][0] == 0
    assert client.get(f"/api/v1/assets/{asset['id']}/georeferencing").json()["current"]["georeferenced"] is False


def test_bad_requests_are_refused(client, project, headers, run_jobs):
    asset, _ = scan(client, project, headers, run_jobs)
    url = f"/api/v1/assets/{asset['id']}/control-points/validate"
    assert client.post(url, json=body(points()[:2])).status_code == 422  # fewer than three
    assert client.post(url, json={**body(points()), "source": ""}).status_code == 422
    assert client.post(url, json={**body(points()), "reason": " "}).status_code == 422
    assert client.post(url, json=body(points(), crs="EPSG:99999")).json()["code"] == "CRS_UNRESOLVED"
    assert client.post(url, json=body(points(), crs="UTM 43N")).status_code == 422  # a name is not a registry code
    assert client.post(url, json=body(points(), crs="EPSG:4979")).json()["code"] == "CRS_UNRESOLVED"  # 3D geographic
    outside = body(points(cells=[(0, 0), (401, 0), (400, 300), (0, 300)]))
    result = client.post(url, json=outside).json()
    assert result["valid"] is False and result["fit"] is None
    assert {c["code"] for c in result["checks"] if c["status"] == "failed"} == {"CONTROL_POINTS_INSIDE_IMAGE"}
    conflict = client.post(
        f"/api/v1/assets/{asset['id']}/control-points",
        json={**body(points()), "expected_version": 4, "confirm": True},
        headers=headers,
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "GEOREFERENCE_VERSION_CONFLICT"
    anonymous = client.post(
        f"/api/v1/assets/{asset['id']}/control-points", json={**body(points()), "expected_version": 0, "confirm": True}
    )
    assert anonymous.status_code == 401
    assert rows("SELECT count(*) FROM georef_assertion")[0][0] == 0


def test_a_sheet_that_is_already_georeferenced_is_not_overridden(client, project, headers, run_jobs):
    placed = put(client, project, headers, run_jobs, "ok.tif", media.geotiff_bytes(crs=UTM), series="osm")["asset"]
    response = client.post(f"/api/v1/assets/{placed['id']}/control-points/validate", json=body(points()))
    assert response.status_code == 409 and response.json()["code"] == "ALREADY_GEOREFERENCED"


# --- transformation safety --------------------------------------------------------------------------------------------

THAILAND = (100.1, 14.1)
DELHI = (77.2, 27.1)


def points_near(crs, lon, lat, pixel=10.0):
    x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)
    gt = [round(x / pixel) * pixel, pixel, 0.0, round(y / pixel) * pixel, 0.0, -pixel]
    return points(gt)


def test_control_points_in_a_crs_that_needs_a_datum_choice_require_the_choice(client, project, headers, run_jobs):
    asset, _ = scan(client, project, headers, run_jobs)
    payload = body(points_near("EPSG:24047", *THAILAND), crs="EPSG:24047")
    result = validate(client, asset, payload)
    assert result["valid"] is False
    plan = result["validation"]["transformation_plan"]
    assert plan["status"] == "selection_required"
    assert {o["id"] for o in plan["operations"] if o["usable"]} == {"EPSG:1812", "EPSG:1304"}
    assert codes(result)["TRANSFORMATION_AVAILABLE"] == "failed"

    chosen = validate(client, asset, {**payload, "transformation_operation": "EPSG:1812"})
    assert chosen["valid"] is True, chosen["checks"]
    done = client.post(
        f"/api/v1/assets/{asset['id']}/control-points",
        json={**payload, "transformation_operation": "EPSG:1812", "expected_version": 0, "confirm": True},
        headers=headers,
    )
    assert done.status_code == 201, done.text
    transformation = done.json()["history"][0]["validation"]["transformation"]
    assert transformation["operation"]["id"] == "EPSG:1812" and transformation["accuracy_m"] == 3.0


def test_control_points_in_a_ballpark_only_crs_are_refused(client, project, headers, run_jobs):
    asset, _ = scan(client, project, headers, run_jobs)
    payload = body(points_near("EPSG:24371", *DELHI), crs="EPSG:24371")  # Kalianpur 1880 zone I: ballpark only
    result = validate(client, asset, payload)
    assert result["valid"] is False
    assert result["validation"]["transformation_plan"]["status"] == "unavailable"
    refused = client.post(
        f"/api/v1/assets/{asset['id']}/control-points",
        json={**payload, "expected_version": 0, "confirm": True},
        headers=headers,
    )
    assert refused.status_code == 422
    assert rows("SELECT count(*) FROM georef_assertion")[0][0] == 0
