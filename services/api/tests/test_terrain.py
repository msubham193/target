"""The 3D terrain view's data: which rasters are elevation, and the height grid made from them.

Synthetic GeoTIFFs written in memory with known values, CRS and geotransform; expected numbers are computed
independently of
the code under test. The tests that matter most are the refusals: colour imagery, 8-bit pictures and multi-band
imagery are
never offered as terrain, a missing unit or an unconfirmed band is never guessed, and nothing is invented for no-data
cells.

Scratch database geopoc_test and scratch data folder only (see conftest.py).
"""

import hashlib

import numpy as np
import pyproj
import pytest
from affine import Affine
from rasterio.enums import ColorInterp
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from sqlalchemy import text

from geopoc.config import get_settings
from geopoc.db.session import get_engine
from geopoc.storage import store
from tests import media
from tests.test_assets import rows, upload
from tests.test_georeferencing import REASON, record

UTM = from_origin(500000, 3000000, 10, 10)  # EPSG:32643, north-up, 10 m cells
UNKNOWN_UUID = "00000000-0000-4000-8000-00000000dead"
ROWS, COLS = 40, 60


def surface(rows=ROWS, cols=COLS, dtype="float32"):
    r, c = np.mgrid[0:rows, 0:cols]
    return (100 + r * 2 + c * 0.5).astype(dtype)


def raster_bytes(
    data=None,
    *,
    crs="EPSG:32643",
    transform=UTM,
    nodata=None,
    units=None,
    description=None,
    scale=None,
    offset=None,
    colour=None,
    count=1,
    dtype="float32",
) -> bytes:
    if data is None:
        data = np.stack([surface(dtype=dtype)] * count)
    elif data.ndim == 2:
        data = data[None]
    bands, height, width = data.shape
    profile = {"driver": "GTiff", "width": width, "height": height, "count": bands, "dtype": str(data.dtype)}
    if crs is not None:
        profile["crs"] = crs
    if transform is not None:
        profile["transform"] = transform
    if nodata is not None:
        profile["nodata"] = nodata
    with MemoryFile() as memory:
        with memory.open(**profile) as dst:
            dst.write(data)
            if colour:
                dst.colorinterp = colour
            if units:
                dst.units = [units] * bands
            if description:
                dst.set_band_description(1, description)
            if scale is not None:
                dst.scales = [scale] * bands
                dst.offsets = [offset or 0.0] * bands
        return memory.read()


def put(client, project, headers, run_jobs, data: bytes, name="dem.tif"):
    response = upload(client, project, headers, name, data, modality="overhead_raster")
    assert response.status_code == 201, response.text
    run_jobs()
    return response.json()["asset"]


def base(project) -> str:
    return f"/api/v1/projects/{project['id']}"


def terrain(client, project, asset, **params):
    return client.get(f"{base(project)}/assets/{asset['id']}/terrain", params=params)


def grid(client, project, asset, **params):
    response = client.get(f"{base(project)}/assets/{asset['id']}/terrain/grid", params=params)
    return response


def values_of(response):
    return np.frombuffer(response.content, dtype="<f4")


def listing(client, project):
    response = client.get(f"{base(project)}/terrain")
    assert response.status_code == 200, response.text
    return response.json()


def verdicts(client, project):
    return {i["filename"]: i for i in listing(client, project)["items"]}


DECLARED = {"units": "metre", "description": "elevation"}


# --- what is offered as terrain -------------------------------------------------------------------------------------


def test_only_rasters_with_elevation_are_offered_and_imagery_never_is(client, project, headers, run_jobs):
    rgb = [ColorInterp.red, ColorInterp.green, ColorInterp.blue]
    files = {
        "declared.tif": raster_bytes(**DECLARED),
        "undeclared.tif": raster_bytes(),
        "geographic.tif": raster_bytes(crs="EPSG:4326", transform=from_origin(75, 27, 0.001, 0.001), units="metre"),
        "rgb.tif": raster_bytes(np.stack([surface(dtype="uint8")] * 3), colour=rgb),
        "grey8.tif": raster_bytes(surface(dtype="uint8")),
        "imagery16.tif": raster_bytes(np.stack([surface(dtype="uint16")] * 3)),
        "unreferenced.tif": raster_bytes(crs=None, transform=None, **DECLARED),
        "rotated.tif": raster_bytes(transform=Affine(10, 2, 500000, 2, -10, 3000000), **DECLARED),
        "southup.tif": raster_bytes(transform=Affine(10, 0, 500000, 0, 10, 2999000), **DECLARED),
    }
    for name, data in files.items():
        put(client, project, headers, run_jobs, data, name)
    found = verdicts(client, project)

    assert found["declared.tif"]["status"] == "usable"
    assert found["geographic.tif"]["status"] == "usable"
    assert found["undeclared.tif"]["status"] == "needs_confirmation"
    band = found["declared.tif"]["bands"][0]
    assert (band["role"], band["declared_unit"]) == ("elevation_declared", "metre")
    assert (
        found["undeclared.tif"]["bands"][0]["role"] == "possible"
        and found["undeclared.tif"]["bands"][0]["declared_unit"] is None
    )
    # colour imagery is never elevation, whatever its values are
    for name in ("rgb.tif", "grey8.tif", "imagery16.tif"):
        assert found[name]["status"] == "not_elevation", name
        assert all(b["role"] == "not_elevation" for b in found[name]["bands"]), name
    assert "image colour" in " ".join(found["rgb.tif"]["bands"][0]["reasons"])
    assert "picture brightness" in " ".join(found["grey8.tif"]["bands"][0]["reasons"])
    assert "Imagery bands are never treated as elevation" in " ".join(found["imagery16.tif"]["bands"][0]["reasons"])
    # a raster that cannot be placed is not drawn, even if it is declared as elevation
    assert (
        found["unreferenced.tif"]["status"] == "unusable"
        and "no map position" in found["unreferenced.tif"]["reasons"][0]
    )
    assert found["rotated.tif"]["status"] == "unusable" and "rotated or sheared" in found["rotated.tif"]["reasons"][0]
    assert found["southup.tif"]["status"] == "unusable" and "north to south" in found["southup.tif"]["reasons"][0]
    body = listing(client, project)
    assert (body["usable"], body["needs_confirmation"], body["not_available"]) == (2, 1, 6)
    assert "never elevation" in body["note"] and body["max_grid_side"] == 512


def test_a_project_without_rasters_or_elevation_has_an_empty_honest_list(client, project, headers, run_jobs):
    empty = listing(client, project)
    assert empty["items"] == [] and (empty["usable"], empty["needs_confirmation"], empty["not_available"]) == (0, 0, 0)
    upload(client, project, headers, "p.jpg", media.image_bytes("JPEG", seed=2))
    assert listing(client, project)["items"] == []  # photographs are not terrain
    put(client, project, headers, run_jobs, raster_bytes(np.stack([surface(dtype="uint16")] * 3)), "imagery.tif")
    only_imagery = listing(client, project)
    assert only_imagery["usable"] == 0 and only_imagery["not_available"] == 1


def test_a_raster_too_large_to_read_is_refused_with_the_reason(client, project, headers, run_jobs, monkeypatch):
    asset = put(client, project, headers, run_jobs, raster_bytes(**DECLARED))
    monkeypatch.setattr(get_settings(), "terrain_max_source_pixels", 1000)
    found = verdicts(client, project)["dem.tif"]
    assert found["status"] == "unusable" and "over the 1,000" in found["reasons"][0]
    refused = terrain(client, project, asset)
    assert (refused.status_code, refused.json()["code"]) == (422, "TERRAIN_UNUSABLE")
    assert grid(client, project, asset).status_code == 422


def test_unsupported_horizontal_units_and_projections_are_refused(client, project, headers, run_jobs):
    # geographic CRS expressed in grads, not degrees
    asset = put(
        client,
        project,
        headers,
        run_jobs,
        raster_bytes(crs="EPSG:4807", transform=from_origin(2, 49, 0.001, 0.001), **DECLARED),
    )
    found = verdicts(client, project)["dem.tif"]
    assert found["status"] == "unusable" and "not degrees" in found["reasons"][0]
    assert terrain(client, project, asset).json()["code"] == "TERRAIN_UNUSABLE"


# --- the grid -------------------------------------------------------------------------------------------------------


def test_the_grid_holds_the_real_source_values_at_full_resolution(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, raster_bytes(**DECLARED))
    meta = terrain(client, project, asset, max_side=64).json()
    assert meta["grid"]["width"] == COLS and meta["grid"]["height"] == ROWS and meta["grid"]["decimation"] == 1
    response = grid(client, project, asset, max_side=64)
    assert response.status_code == 200 and response.headers["content-type"] == "application/octet-stream"
    got = values_of(response)
    assert (
        got.size == ROWS * COLS
        and response.headers["x-grid-width"] == str(COLS)
        and response.headers["x-grid-height"] == str(ROWS)
    )
    assert np.array_equal(got.reshape(ROWS, COLS), surface())  # exactly the stored values, north row first
    assert (
        response.headers["etag"]
        and grid(client, project, asset, max_side=64).headers["etag"] == response.headers["etag"]
    )
    assert meta["grid_url"].endswith("terrain/grid?band=1&max_side=64") and "NaN means no data" in meta["grid_format"]


def test_a_large_raster_is_decimated_by_taking_real_pixels_never_by_averaging(client, project, headers, run_jobs):
    data = surface(80, 120)
    asset = put(client, project, headers, run_jobs, raster_bytes(data, **DECLARED))
    meta = terrain(client, project, asset, max_side=16).json()
    step = 8  # ceil(120 / 16)
    assert meta["grid"] == {**meta["grid"], "width": 15, "height": 10, "decimation": step}
    assert "nothing is averaged" in meta["grid"]["method"]
    got = values_of(grid(client, project, asset, max_side=16)).reshape(10, 15)
    source = set(np.unique(data).tolist())
    assert set(np.unique(got).tolist()) <= source  # every grid value is a value that exists in the source
    # and it is the pixel at the middle of each block, so the surface lines up with where it is
    rr, cc = np.meshgrid(np.arange(10), np.arange(15), indexing="ij")
    assert np.array_equal(got, data[(rr * step + step // 2).clip(max=79), (cc * step + step // 2).clip(max=119)])
    # a cell now covers 8 x 8 source pixels
    assert meta["cell_size_native"] == [80.0, 80.0] and meta["cell_size_m"] == [80.0, 80.0]
    assert meta["source_size"] == [120, 80]


def test_the_grid_size_is_bounded(client, project, headers, run_jobs, monkeypatch):
    asset = put(client, project, headers, run_jobs, raster_bytes(**DECLARED))
    assert terrain(client, project, asset, max_side=513).status_code == 422
    assert terrain(client, project, asset, max_side=8).status_code == 422
    monkeypatch.setattr(get_settings(), "terrain_max_grid_side", 64)
    over = terrain(client, project, asset, max_side=128)
    assert (over.status_code, over.json()["code"]) == (422, "TERRAIN_GRID_SIZE")


# --- no data --------------------------------------------------------------------------------------------------------


def test_no_data_cells_are_holes_not_terrain_and_do_not_count_in_the_statistics(client, project, headers, run_jobs):
    data = surface()
    data[5:10, 5:10] = -9999
    asset = put(client, project, headers, run_jobs, raster_bytes(data, nodata=-9999, **DECLARED))
    meta = terrain(client, project, asset, max_side=64).json()
    got = values_of(grid(client, project, asset, max_side=64)).reshape(ROWS, COLS)
    assert np.isnan(got[5:10, 5:10]).all() and np.isnan(got).sum() == 25
    assert np.array_equal(got[~np.isnan(got)], data[data != -9999])  # nothing was filled in
    e = meta["elevation"]
    valid = data[data != -9999]
    assert (e["valid_cells"], e["nodata_cells"], e["nodata_value"]) == (ROWS * COLS - 25, 25, -9999.0)
    assert (e["min"], e["max"]) == (float(valid.min()), float(valid.max())) and e["mean"] == pytest.approx(
        float(valid.mean()), rel=1e-6
    )
    assert meta["warnings"] == [] and any("holes" in d for d in meta["disclosures"])


def test_nan_values_are_no_data_even_when_none_is_declared(client, project, headers, run_jobs):
    data = surface()
    data[0, :3] = np.nan
    asset = put(client, project, headers, run_jobs, raster_bytes(data, **DECLARED))
    got = values_of(grid(client, project, asset, max_side=64)).reshape(ROWS, COLS)
    assert np.isnan(got[0, :3]).all() and not np.isnan(got[1:]).any()
    assert terrain(client, project, asset, max_side=64).json()["elevation"]["nodata_cells"] == 3


def test_a_marker_value_without_a_declared_no_data_is_reported_but_not_removed(client, project, headers, run_jobs):
    data = surface()
    data[2, 2] = -9999
    asset = put(client, project, headers, run_jobs, raster_bytes(data, **DECLARED))
    meta = terrain(client, project, asset, max_side=64).json()
    assert any("-9999" in w and "declares no no-data value" in w for w in meta["warnings"])
    assert (
        values_of(grid(client, project, asset, max_side=64)).reshape(ROWS, COLS)[2, 2] == -9999
    )  # not fabricated away
    assert meta["elevation"]["min"] == -9999.0


def test_a_raster_that_is_all_no_data_has_no_terrain(client, project, headers, run_jobs):
    asset = put(
        client,
        project,
        headers,
        run_jobs,
        raster_bytes(np.full((ROWS, COLS), -9999, dtype="float32"), nodata=-9999, **DECLARED),
    )
    for response in (terrain(client, project, asset), grid(client, project, asset)):
        assert (response.status_code, response.json()["code"]) == (422, "TERRAIN_NO_VALID_DATA")


def test_a_flat_surface_is_drawn_and_said_to_be_flat(client, project, headers, run_jobs):
    asset = put(
        client, project, headers, run_jobs, raster_bytes(np.full((ROWS, COLS), 50, dtype="float32"), **DECLARED)
    )
    assert any("flat" in w for w in terrain(client, project, asset).json()["warnings"])


# --- units and what the file says -----------------------------------------------------------------------------------


def test_a_declared_unit_is_used_and_credited_to_the_file(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, raster_bytes(**DECLARED))
    e = terrain(client, project, asset).json()["elevation"]
    assert (e["unit"], e["unit_label"], e["unit_source"], e["role_source"], e["to_metre"]) == (
        "metre",
        "m",
        "declared",
        "declared",
        1.0,
    )


def test_feet_are_kept_as_feet_with_the_conversion_stated(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, raster_bytes(units="ft", description="elevation"))
    meta = terrain(client, project, asset, max_side=64).json()
    assert meta["elevation"]["unit"] == "foot" and meta["elevation"]["to_metre"] == pytest.approx(0.3048)
    assert np.array_equal(
        values_of(grid(client, project, asset, max_side=64)).reshape(ROWS, COLS), surface()
    )  # values untouched


def test_a_band_the_file_does_not_declare_needs_a_person_to_confirm_it_and_state_the_unit(
    client, project, headers, run_jobs
):
    asset = put(client, project, headers, run_jobs, raster_bytes())
    first = terrain(client, project, asset)
    assert (first.status_code, first.json()["code"]) == (409, "ELEVATION_NOT_CONFIRMED")
    assert "Confirm it is" in first.json()["detail"]
    second = terrain(client, project, asset, confirm_elevation="true")
    assert (second.status_code, second.json()["code"]) == (409, "ELEVATION_UNIT_UNDECLARED")
    assert terrain(client, project, asset, unit="metre").json()["code"] == "ELEVATION_NOT_CONFIRMED"
    assert grid(client, project, asset, unit="metre").status_code == 409  # the grid is held back the same way

    ok = terrain(client, project, asset, confirm_elevation="true", unit="foot")
    assert ok.status_code == 200, ok.text
    meta = ok.json()
    assert (
        meta["elevation"]["unit_source"] == "stated_for_this_view"
        and meta["elevation"]["role_source"] == "confirmed_for_this_view"
    )
    text_ = " ".join(meta["disclosures"])
    assert "stated by a person for this view only" in text_ and "confirmed it for this view only" in text_
    assert "confirm_elevation=true" in meta["grid_url"] and "unit=foot" in meta["grid_url"]
    assert grid(client, project, asset, confirm_elevation="true", unit="foot").status_code == 200
    # the statement is not stored: the raster is still "needs confirmation" afterwards
    assert verdicts(client, project)["dem.tif"]["status"] == "needs_confirmation"


def test_a_person_cannot_override_a_unit_the_file_declares(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, raster_bytes(**DECLARED))
    clash = terrain(client, project, asset, unit="foot")
    assert (clash.status_code, clash.json()["code"]) == (422, "UNIT_CONFLICT")
    assert terrain(client, project, asset, unit="metre").status_code == 200  # agreeing is fine
    assert terrain(client, project, asset, unit="furlong").status_code == 422


def test_a_declared_scale_and_offset_are_applied_and_said_so(client, project, headers, run_jobs):
    raw = (surface() * 10).astype("int16")
    asset = put(client, project, headers, run_jobs, raster_bytes(raw, scale=0.1, offset=5.0, **DECLARED))
    meta = terrain(client, project, asset, max_side=64).json()
    assert meta["elevation"]["declared_scale_offset_applied"] is True and any(
        "scale and offset" in d for d in meta["disclosures"]
    )
    got = values_of(grid(client, project, asset, max_side=64)).reshape(ROWS, COLS)
    assert np.allclose(got, raw * 0.1 + 5.0, atol=1e-3)


def test_imagery_is_refused_by_the_grid_endpoint_too(client, project, headers, run_jobs):
    rgb = put(
        client,
        project,
        headers,
        run_jobs,
        raster_bytes(
            np.stack([surface(dtype="uint8")] * 3), colour=[ColorInterp.red, ColorInterp.green, ColorInterp.blue]
        ),
        "rgb.tif",
    )
    grey = put(client, project, headers, run_jobs, raster_bytes(surface(dtype="uint8")), "grey.tif")
    multi = put(client, project, headers, run_jobs, raster_bytes(np.stack([surface(dtype="uint16")] * 4)), "multi.tif")
    for asset in (rgb, grey, multi):
        for call in (terrain, grid):
            response = call(client, project, asset, confirm_elevation="true", unit="metre")  # even if a person insists
            assert (response.status_code, response.json()["code"]) == (422, "NOT_ELEVATION"), asset["original_filename"]


def test_only_a_candidate_band_can_be_chosen(client, project, headers, run_jobs):
    asset = put(
        client, project, headers, run_jobs, raster_bytes(np.stack([surface(), surface() + 1]), description="elevation")
    )
    # the declaration is on band 1 only (and states no unit); band 2 is not a candidate
    assert terrain(client, project, asset, band=1).json()["code"] == "ELEVATION_UNIT_UNDECLARED"
    assert (
        terrain(client, project, asset, band=1, unit="metre").status_code == 200
    )  # declared as elevation: no confirmation needed
    bad = terrain(client, project, asset, band=2)
    assert (bad.status_code, bad.json()["code"]) == (422, "BAND_NOT_ELEVATION")


# --- placement and context ------------------------------------------------------------------------------------------


def test_a_projected_raster_is_placed_exactly_in_its_own_crs(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, raster_bytes(**DECLARED))
    meta = terrain(client, project, asset, max_side=64).json()
    assert (
        meta["crs_id"] == "EPSG:32643"
        and meta["horizontal_unit"] == "linear"
        and meta["georeferencing_source"] == "file"
    )
    assert meta["extent_native"] == {"x_min": 500000.0, "y_max": 3000000.0, "x_max": 500600.0, "y_min": 2999600.0}
    assert meta["cell_size_m"] == [10.0, 10.0] and meta["extent_m"] == [600.0, 400.0]
    assert meta["horizontal"]["approximate"] is False and any("not reprojected" in d for d in meta["disclosures"])
    to_geo = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    lons, lats = zip(*(to_geo.transform(x, y) for x in (500000, 500600) for y in (2999600, 3000000)), strict=True)
    assert meta["bbox_4326"] == pytest.approx([min(lons), min(lats), max(lons), max(lats)], abs=1e-9)
    assert meta["vertical_datum"] == {"state": "not_stated", "name": None}
    assert any("vertical datum" in d for d in meta["disclosures"])
    assert meta["source_checksum"] == asset["sha256"] and meta["filename"] == "dem.tif"


def test_a_geographic_raster_gets_an_approximate_metre_size_and_says_so(client, project, headers, run_jobs):
    asset = put(
        client,
        project,
        headers,
        run_jobs,
        raster_bytes(
            crs="EPSG:4326", transform=from_origin(75, 27, 0.001, 0.001), units="metre", description="elevation"
        ),
    )
    meta = terrain(client, project, asset, max_side=64).json()
    assert meta["horizontal_unit"] == "degree" and meta["horizontal"]["approximate"] is True
    centre_lat = 27 - 0.001 * ROWS / 2
    geod = pyproj.Geod(ellps="WGS84")
    expect_x = geod.inv(75 + 0.03, centre_lat, 75 + 0.031, centre_lat)[2]
    expect_y = geod.inv(75.03, centre_lat, 75.03, centre_lat + 0.001)[2]
    assert meta["cell_size_m"] == pytest.approx([expect_x, expect_y], rel=1e-6)
    assert (
        95 < meta["cell_size_m"][0] < 102 and 109 < meta["cell_size_m"][1] < 113
    )  # ~99 m east-west, ~110.6 m north-south at 27 N
    assert any("distances are approximate" in d for d in meta["disclosures"])


def test_recorded_georeferencing_makes_an_unplaced_raster_drawable_and_is_labelled_as_recorded(
    client, project, headers, run_jobs, profile
):
    asset = put(client, project, headers, run_jobs, raster_bytes(crs=None, **DECLARED))
    assert verdicts(client, project)["dem.tif"]["status"] == "unusable"
    assert terrain(client, project, asset).status_code == 422
    done = record(client, headers, asset, operation="assign_crs", crs_id="EPSG:32643", reason=REASON)
    assert done.status_code == 201, done.text
    assert verdicts(client, project)["dem.tif"]["status"] == "usable"
    meta = terrain(client, project, asset, max_side=64).json()
    assert meta["georeferencing_source"] == "recorded" and meta["crs_id"] == "EPSG:32643"
    assert meta["extent_native"]["x_min"] == 500000.0


# --- project isolation and errors -----------------------------------------------------------------------------------


def test_a_project_sees_only_its_own_rasters(client, project, headers, run_jobs):
    mine = put(client, project, headers, run_jobs, raster_bytes(**DECLARED), "mine.tif")
    other = client.post("/api/v1/projects", json={"name": "Other"}, headers=headers).json()
    theirs = put(client, other, headers, run_jobs, raster_bytes(surface() + 500, **DECLARED), "theirs.tif")

    assert set(verdicts(client, project)) == {"mine.tif"} and set(verdicts(client, other)) == {"theirs.tif"}
    for url in (f"{base(other)}/assets/{mine['id']}/terrain", f"{base(other)}/assets/{mine['id']}/terrain/grid"):
        response = client.get(url)
        assert (response.status_code, response.json()["code"]) == (404, "ASSET_NOT_FOUND"), url
    # each project's own raster gives its own values
    assert values_of(grid(client, project, mine, max_side=64)).min() == pytest.approx(100.0)
    assert values_of(grid(client, other, theirs, max_side=64)).min() == pytest.approx(600.0)
    assert client.get(f"/api/v1/projects/{UNKNOWN_UUID}/terrain").status_code == 404
    assert client.get(f"/api/v1/projects/{UNKNOWN_UUID}/assets/{mine['id']}/terrain").status_code == 404
    assert client.get(f"{base(project)}/assets/{UNKNOWN_UUID}/terrain").status_code == 404


def test_a_photograph_or_video_is_not_a_terrain_source(client, project, headers):
    photo = upload(client, project, headers, "p.jpg", media.image_bytes("JPEG", seed=3)).json()["asset"]
    response = terrain(client, project, photo)
    assert (response.status_code, response.json()["code"]) == (409, "NOT_A_RASTER")
    assert grid(client, project, photo).status_code == 409


def test_a_missing_stored_original_is_a_clear_error(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, raster_bytes(**DECLARED))
    path = store.absolute_path(rows("SELECT storage_path FROM blob")[0][0])
    path.chmod(0o666)
    path.unlink()
    assert (terrain(client, project, asset).status_code, terrain(client, project, asset).json()["code"]) == (
        500,
        "STORAGE_ERROR",
    )


# --- read-only ----------------------------------------------------------------------------------------------------------------


def test_viewing_terrain_writes_nothing_and_leaves_the_original_unchanged(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, raster_bytes(**DECLARED))
    path = store.absolute_path(rows("SELECT storage_path FROM blob")[0][0])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    files = sorted(p for p in store.data_root().rglob("*") if p.is_file())

    def counts():
        with get_engine().connect() as conn:
            return {
                t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
                for t in ("job", "audit_event", "artifact", "asset", "blob", "observation")
            }

    before = counts()
    for _ in range(2):
        listing(client, project)
        terrain(client, project, asset)
        grid(client, project, asset)
        terrain(client, project, asset, band=3)  # a refusal writes nothing either
    assert counts() == before and run_jobs() == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest == asset["sha256"]
    assert sorted(p for p in store.data_root().rglob("*") if p.is_file()) == files
