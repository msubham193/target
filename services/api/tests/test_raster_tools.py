"""Phase 3b: non-AI raster tools — band statistics, display settings, pixel values, measurement, display copies
and pixel comparison.

Synthetic rasters only, written in memory with known values, CRS and geotransform. Expected numbers are computed
independently (from the arrays written, or from known geodesy such as the UTM scale factor) rather than by calling
the code under test. No real-world imagery is involved.
"""

import hashlib
import io
import math

import numpy as np
import pyproj
import pytest
import rasterio
from PIL import Image
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from geopoc.config import get_settings
from geopoc.db.session import get_engine
from geopoc.geo import displaycopy, tiles
from tests.test_assets import upload

NORTH_UP = from_origin(500000, 3000000, 10, 10)  # EPSG:32643, on the central meridian (75 E)
TO_LONLAT = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
K0 = 0.9996  # UTM scale factor on the central meridian


def tif(data: np.ndarray, *, crs="EPSG:32643", transform=NORTH_UP, nodata=None, descriptions=None, scales=None,
        offsets=None, units=None, overviews=False) -> bytes:  # fmt: skip
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
            for i, value in enumerate(descriptions or []):
                dst.set_band_description(i + 1, value)
            if scales:
                dst.scales = scales
            if offsets:
                dst.offsets = offsets
            if units:
                dst.units = units
            if overviews:
                dst.build_overviews([2, 4])
        return memory.read()


def put(client, project, headers, run_jobs, data: bytes, name="scene.tif"):
    response = upload(client, project, headers, name, data, modality="overhead_raster")
    assert response.status_code == 201, response.text
    statuses = run_jobs()
    return response.json()["asset"], statuses


def tools(client, asset):
    response = client.get(f"/api/v1/assets/{asset['id']}/raster-tools")
    assert response.status_code == 200, response.text
    return response.json()


def gradient(bands=3, height=60, width=80, dtype="uint16", scale=1):
    rows, cols = np.mgrid[0:height, 0:width]
    return np.stack([((rows * width + cols) * scale + b * 1000).astype(dtype) for b in range(bands)])


# --- 1. band information and statistics ---------------------------------------------------------------------------------


def test_statistics_are_exact_and_band_identity_is_only_as_declared(client, project, headers, run_jobs):
    data = gradient()
    data[0, :5, :5] = 0
    asset, statuses = put(
        client, project, headers, run_jobs,
        tif(data, nodata=0, descriptions=["near infrared", "", "red"], units=["", "", "reflectance"]),
    )  # fmt: skip
    assert statuses == ["completed", "completed"]  # preview, statistics
    result = tools(client, asset)
    stats = result["statistics"]
    assert stats["method"] == "exact" and stats["fraction_of_pixels"] == 1.0
    band1 = stats["bands"][0]
    valid = data[0][data[0] != 0].astype("float64")
    assert band1["valid"] == valid.size and band1["nodata_or_masked"] == data[0].size - valid.size
    assert band1["min"] == valid.min() and band1["max"] == valid.max()
    assert band1["mean"] == pytest.approx(valid.mean()) and band1["std"] == pytest.approx(valid.std())
    assert band1["percentiles"]["2"] == pytest.approx(np.percentile(valid, 2))
    assert sum(band1["histogram"]["counts"]) == valid.size
    # What each band is comes from the file, never from its position: band 2 has no description.
    assert [b["description"] for b in result["bands"]] == ["near infrared", None, "red"]
    assert result["bands"][2]["units"] == "reflectance" and result["bands"][0]["units"] is None
    assert "never inferred" in result["note"]
    assert result["display_copy_needed"] is False and result["display_copy"] is None


def test_large_rasters_use_a_recorded_sample(client, project, headers, run_jobs, monkeypatch):
    monkeypatch.setattr(get_settings(), "statistics_exact_max_pixels", 1000)
    asset, _ = put(client, project, headers, run_jobs, tif(gradient(bands=1, height=100, width=100)))
    stats = tools(client, asset)["statistics"]
    assert stats["method"] == "sample" and stats["fraction_of_pixels"] < 0.11
    assert "estimates" in stats["method_note"]
    # Sampled values are real pixel values (nearest neighbour), so they lie on the written grid.
    assert stats["bands"][0]["min"] % 1 == 0


def test_statistics_can_be_recomputed_and_unsupported_types_fail_clearly(client, project, headers, run_jobs):
    asset, _ = put(client, project, headers, run_jobs, tif(gradient(bands=1)))
    first = client.post(f"/api/v1/assets/{asset['id']}/statistics", headers=headers)
    assert first.status_code == 202 and first.json()["status"] == "queued"
    again = client.post(f"/api/v1/assets/{asset['id']}/statistics", headers=headers)
    assert again.status_code == 409 and again.json()["code"] == "JOB_ALREADY_ACTIVE"
    assert run_jobs() == ["completed"]
    assert client.post(f"/api/v1/assets/{asset['id']}/statistics").status_code == 401

    complex_raster, statuses = put(
        client, project, headers, run_jobs, tif(np.ones((1, 8, 8), dtype="complex64")), name="c.tif"
    )
    assert statuses == ["failed", "failed"]
    job = tools(client, complex_raster)["statistics_job"]
    assert job["error_code"] == "STATISTICS_UNSUPPORTED"


# --- 2. display settings ---------------------------------------------------------------------------------------------------


def test_bands_and_stretch_change_only_the_picture(client, project, headers, run_jobs):
    data = np.stack([np.full((40, 50), v, dtype="uint8") for v in (10, 20, 30)])
    asset, _ = put(client, project, headers, run_jobs, tif(data))
    url = f"/api/v1/assets/{asset['id']}/render.png"
    as_stored = np.asarray(Image.open(io.BytesIO(client.get(url, params={"stretch": "none"}).content)))
    assert tuple(as_stored[5, 5, :3]) == (10, 20, 30)
    swapped = np.asarray(Image.open(io.BytesIO(client.get(url, params={"bands": "3,2,1", "stretch": "none"}).content)))
    assert tuple(swapped[5, 5, :3]) == (30, 20, 10)
    grey = np.asarray(Image.open(io.BytesIO(client.get(url, params={"bands": "2", "stretch": "none"}).content)))
    assert tuple(grey[5, 5, :3]) == (20, 20, 20)
    manual = client.get(url, params={"bands": "1", "stretch": "manual", "low": "0", "high": "20"})
    assert tuple(np.asarray(Image.open(io.BytesIO(manual.content)))[5, 5, :3]) == (127, 127, 127)
    # The stored values are unchanged by any of this.
    pixel = client.get(f"/api/v1/assets/{asset['id']}/pixel", params={"col": 5, "row": 5}).json()
    assert [v["raw"] for v in pixel["values"]] == [10, 20, 30]


def test_stretches_come_from_band_statistics_and_are_validated(client, project, headers, run_jobs):
    data = gradient(bands=3)
    asset, _ = put(client, project, headers, run_jobs, tif(data))
    url = f"/api/v1/assets/{asset['id']}/display"
    resolved = client.get(url, params={"bands": "3,2,1", "stretch": "p2-98"}).json()
    assert resolved["bands"] == [3, 2, 1]
    expected_low = np.percentile(data[2].astype("float64"), 2)
    assert resolved["ranges"][0][0] == pytest.approx(expected_low)
    assert "percentiles" in resolved["description"] and "Display only" in resolved["note"]
    minmax = client.get(url, params={"stretch": "minmax", "bands": "1"}).json()
    assert minmax["ranges"] == [[float(data[0].min()), float(data[0].max())]]
    for params, code in (
        ({"bands": "4"}, "DISPLAY_INVALID"),
        ({"bands": "1,2"}, "DISPLAY_INVALID"),
        ({"stretch": "none"}, "DISPLAY_INVALID"),  # 16-bit values have no direct screen meaning
        ({"stretch": "manual", "low": "5", "high": "1"}, "DISPLAY_INVALID"),
        ({"stretch": "manual", "low": "1", "high": "5"}, "DISPLAY_INVALID"),  # one pair for three bands
        ({"stretch": "sigmoid"}, "DISPLAY_INVALID"),
    ):
        response = client.get(url, params=params)
        assert response.status_code == 422 and response.json()["code"] == code, params


def test_map_tiles_follow_the_display_settings(client, project, headers, run_jobs):
    data = np.stack([np.full((150, 200), v, dtype="uint8") for v in (10, 20, 30)])
    asset, _ = put(client, project, headers, run_jobs, tif(data))
    lon, lat = TO_LONLAT.transform(501000, 2999250)
    x_m, y_m = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(lon, lat)
    z = 15
    size = 2 * tiles.ORIGIN / 2**z
    x, y = int((x_m + tiles.ORIGIN) / size), int((tiles.ORIGIN - y_m) / size)
    px, py = int(((x_m + tiles.ORIGIN) / size - x) * 256), int(((tiles.ORIGIN - y_m) / size - y) * 256)
    base = f"/api/v1/assets/{asset['id']}/tiles/{z}/{x}/{y}.png"
    normal = np.asarray(Image.open(io.BytesIO(client.get(base).content)))
    swapped = np.asarray(Image.open(io.BytesIO(client.get(base, params={"bands": "3,2,1"}).content)))
    assert tuple(normal[py, px, :3]) == (10, 20, 30) and tuple(swapped[py, px, :3]) == (30, 20, 10)
    assert client.get(base, params={"bands": "9"}).status_code == 422


# --- pixel values -------------------------------------------------------------------------------------------------------------


def test_pixel_values_are_read_from_the_original(client, project, headers, run_jobs):
    data = gradient(bands=2, dtype="int16")
    data[1, 10, 20] = -9999
    asset, _ = put(
        client, project, headers, run_jobs,
        tif(data, nodata=-9999, scales=[0.5, 1.0], offsets=[10.0, 0.0], descriptions=["elevation", ""]),
    )  # fmt: skip
    url = f"/api/v1/assets/{asset['id']}/pixel"
    pixel = client.get(url, params={"col": 20, "row": 10}).json()
    assert pixel["values"][0]["raw"] == int(data[0, 10, 20])
    assert pixel["values"][0]["scaled"] == pytest.approx(data[0, 10, 20] * 0.5 + 10.0)  # as the file declares
    assert pixel["values"][0]["description"] == "elevation"
    assert pixel["values"][1] == {
        "band": 2,
        "raw": None,
        "nodata": True,
        "scaled": None,
        "units": None,
        "description": None,
    }
    # The pixel centre in the CRS, and in longitude/latitude, from the affine transform.
    assert pixel["native"]["x"] == pytest.approx(500000 + 20.5 * 10)
    assert pixel["native"]["y"] == pytest.approx(3000000 - 10.5 * 10)
    assert pixel["lon_lat"] == pytest.approx(list(TO_LONLAT.transform(500205, 2999895)))
    # The same pixel found by its map position.
    by_map = client.get(url, params={"lon": pixel["lon_lat"][0], "lat": pixel["lon_lat"][1]}).json()
    assert (by_map["col"], by_map["row"]) == (20, 10)
    assert client.get(url, params={"col": 80, "row": 0}).status_code == 404
    assert client.get(url).status_code == 422


def test_unplaced_rasters_can_be_inspected_by_pixel_but_not_by_position(client, project, headers, run_jobs):
    asset, _ = put(client, project, headers, run_jobs, tif(gradient(bands=1), crs=None, transform=None))
    pixel = client.get(f"/api/v1/assets/{asset['id']}/pixel", params={"col": 1, "row": 1}).json()
    assert pixel["native"] is None and pixel["lon_lat"] is None and pixel["georeferencing_source"] is None
    refused = client.get(f"/api/v1/assets/{asset['id']}/pixel", params={"lon": 75, "lat": 27})
    assert refused.status_code == 409 and refused.json()["code"] == "CRS_UNRESOLVED"


# --- 3. measurement ---------------------------------------------------------------------------------------------------------


def measure(client, **body):
    return client.post("/api/v1/measurements", json=body)


def test_geodesic_measurement_on_the_map_matches_known_values(client):
    # One degree of longitude along the equator on WGS 84: 111 319.49 m (a = 6 378 137 m, 2πa / 360).
    equator = measure(client, kind="distance", space="lonlat", points=[[0, 0], [1, 0]]).json()
    assert equator["value"] == pytest.approx(2 * math.pi * 6378137 / 360, rel=1e-9)
    assert equator["validity"] == "valid" and equator["unit"] == "m" and "WGS 84" in equator["method"]
    # A 1° × 1° cell at the equator: about 12 309 km² on WGS 84.
    cell = measure(client, kind="area", space="lonlat", points=[[0, 0], [1, 0], [1, 1], [0, 1]]).json()
    assert cell["value"] / 1e6 == pytest.approx(12308.8, rel=1e-3) and cell["unit"] == "m²"
    for points, code in (
        ([[0, 0]], 422),
        ([[0, 0], [1, 1], [1, 0], [0, 1]], 422),  # self-crossing outline
        ([[179.5, 0], [-179.5, 0]], 422),  # across the antimeridian
        ([[0, 95], [1, 0]], 422),
    ):
        kind = "area" if len(points) == 4 else "distance"
        assert measure(client, kind=kind, space="lonlat", points=points).status_code == code


def test_pixel_measurement_uses_the_rasters_georeferencing(client, project, headers, run_jobs):
    asset, _ = put(client, project, headers, run_jobs, tif(gradient(bands=1, height=200, width=200)))
    line = measure(client, kind="distance", space="pixel", asset_id=asset["id"], points=[[0, 100], [100, 100]]).json()
    # 100 pixels of 10 m on the UTM grid = 1 000 m planar; on the ellipsoid, 1 000 / k0 at the central meridian.
    assert line["planar"]["value"] == pytest.approx(1000.0)
    assert line["value"] == pytest.approx(1000.0 / K0, rel=2e-4)
    assert line["pixel_value"] == 100 and line["pixel_unit"] == "pixels"
    assert "WGS 84 / UTM zone 43N" in line["method"] and line["flags"] == []
    square = measure(
        client, kind="area", space="pixel", asset_id=asset["id"], points=[[0, 0], [100, 0], [100, 100], [0, 100]]
    ).json()
    assert square["planar"]["value"] == pytest.approx(1e6)
    assert square["value"] == pytest.approx(1e6 / K0**2, rel=5e-4)
    outside = measure(client, kind="distance", space="pixel", asset_id=asset["id"], points=[[0, 0], [500, 0]])
    assert outside.status_code == 422 and outside.json()["code"] == "POINT_OUTSIDE_RASTER"


def test_no_ground_measurement_without_georeferencing_and_recorded_georeferencing_is_flagged(
    client, project, headers, run_jobs
):
    asset, _ = put(client, project, headers, run_jobs, tif(gradient(bands=1), crs=None))
    unplaced = measure(client, kind="distance", space="pixel", asset_id=asset["id"], points=[[0, 0], [30, 40]]).json()
    assert unplaced["validity"] == "unavailable" and unplaced["value"] is None and unplaced["unit"] is None
    assert unplaced["pixel_value"] == 50 and "not ground units" in unplaced["note"]
    client.post(
        f"/api/v1/assets/{asset['id']}/georeferencing",
        json={"operation": "assign_crs", "crs_id": "EPSG:32643", "reason": "Delivery note.", "expected_version": 0,
              "confirm": True},
        headers=headers,
    )  # fmt: skip
    recorded = measure(client, kind="distance", space="pixel", asset_id=asset["id"], points=[[0, 0], [30, 40]]).json()
    assert recorded["validity"] == "valid" and recorded["planar"]["value"] == pytest.approx(500.0)
    assert recorded["flags"] == ["CRS_USER_ASSERTED"] and "recorded by a person" in recorded["note"]


# --- 4. display copies ------------------------------------------------------------------------------------------------------


def test_a_verified_display_copy_is_built_for_large_rasters_and_draws_the_same_pixels(
    client, project, headers, run_jobs, monkeypatch
):
    monkeypatch.setattr(get_settings(), "display_copy_min_side", 100)
    # Larger than one 512-pixel block, so GDAL builds overview levels for the copy.
    body = tif(
        np.stack([((np.arange(900 * 1200).reshape(900, 1200) * (b + 1)) % 251).astype("uint8") for b in range(3)])
    )
    asset, statuses = put(client, project, headers, run_jobs, body)
    assert statuses == ["completed", "completed", "completed"]  # preview, statistics, display copy
    result = tools(client, asset)
    copy = result["display_copy"]
    assert result["display_source"] == "display_copy"
    assert copy["verification"]["windows_compared"] == 25 and all(copy["verification"]["checks"].values())
    assert copy["verification"]["overview_levels"]  # overviews exist in the copy
    assert copy["source_checksum"] == hashlib.sha256(body).hexdigest()
    # Full-resolution pixels of the copy are identical to the original's, so tiles look the same.
    from geopoc.storage import store

    original = store.absolute_path(store.relative_path(asset["sha256"]))
    copy_path = store.absolute_path(f"derived/display/{asset['id']}/display.tif")
    with rasterio.open(original) as a, rasterio.open(copy_path) as b:
        assert np.array_equal(a.read(), b.read())
    lon, lat = TO_LONLAT.transform(506000, 2995500)
    x_m, y_m = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(lon, lat)
    z = 17
    size = 2 * tiles.ORIGIN / 2**z
    x, y = int((x_m + tiles.ORIGIN) / size), int((tiles.ORIGIN - y_m) / size)
    assert tiles.render_tile(original, (1, 2, 3), (None, None, None), z, x, y) == tiles.render_tile(
        copy_path, (1, 2, 3), (None, None, None), z, x, y
    )
    assert client.get(f"/api/v1/assets/{asset['id']}/content").content == body


def test_a_copy_that_differs_from_the_original_is_not_used(client, project, headers, run_jobs, monkeypatch):
    monkeypatch.setattr(get_settings(), "display_copy_min_side", 50)

    def different(original, copy):
        raise displaycopy.DisplayCopyError("Full-resolution pixels differ from the original (simulated).")

    monkeypatch.setattr(displaycopy, "verify", different)
    asset, statuses = put(
        client, project, headers, run_jobs, tif(gradient(bands=1, dtype="uint8", height=80, width=120))
    )
    assert statuses == ["completed", "completed", "failed"]
    result = tools(client, asset)
    assert result["display_copy"] is None and result["display_source"] == "original"
    assert result["display_copy_job"]["error_code"] == "DISPLAY_COPY_MISMATCH"


def test_rasters_with_their_own_overviews_need_no_copy(client, project, headers, run_jobs, monkeypatch):
    monkeypatch.setattr(get_settings(), "display_copy_min_side", 50)
    asset, statuses = put(
        client, project, headers, run_jobs, tif(gradient(bands=1, dtype="uint8", height=80, width=120), overviews=True)
    )
    assert statuses == ["completed", "completed"]
    assert tools(client, asset)["display_copy_needed"] is False


# --- 5. pixel comparison ------------------------------------------------------------------------------------------------------


def compare(client, project, headers, a, b, **extra):
    return client.post(
        f"/api/v1/projects/{project['id']}/comparisons",
        json={"asset_a": a["id"], "asset_b": b["id"], **extra},
        headers=headers,
    )


def test_aligned_rasters_are_compared_pixel_by_pixel_into_a_derived_raster(client, project, headers, run_jobs):
    before = gradient(bands=2, dtype="uint16")
    after = before.copy()
    after[1, 20:30, 40:50] += 500
    before[1, 0, 0] = 0  # nodata in A becomes nodata in the result
    a, _ = put(client, project, headers, run_jobs, tif(before, nodata=0), name="before.tif")
    b, _ = put(client, project, headers, run_jobs, tif(after), name="after.tif")
    assert client.get(f"/api/v1/assets/{a['id']}/alignment/{b['id']}").json() == {"aligned": True, "reasons": []}

    queued = compare(client, project, headers, a, b, band_a=2, band_b=2, reason="Before and after the works.")
    assert queued.status_code == 202, queued.text
    statuses = run_jobs()
    assert statuses[0] == "completed"  # the comparison; then the derived raster's preview and statistics
    (done,) = client.get(f"/api/v1/projects/{project['id']}/comparisons").json()
    derived_id = done["derived_asset_id"]
    expected = after[1].astype("float64") - before[1].astype("float64")
    valid = np.ones_like(expected, dtype=bool)
    valid[0, 0] = False
    assert done["summary"]["valid_pixels"] == valid.sum()
    assert done["summary"]["max"] == pytest.approx(expected[valid].max())
    assert done["summary"]["mean"] == pytest.approx(expected[valid].mean())

    derived = client.get(f"/api/v1/assets/{derived_id}").json()
    assert "DERIVED_ASSET" in derived["quality_flags"] and derived["raster"]["crs_identifier"] == "EPSG:32643"
    assert derived["original_filename"] == "difference after minus before.tif"
    pixel = client.get(f"/api/v1/assets/{derived_id}/pixel", params={"col": 45, "row": 25}).json()
    assert pixel["values"][0]["raw"] == pytest.approx(expected[25, 45])
    assert pixel["values"][0]["description"].startswith("B (band 2) minus A (band 2)")
    assert client.get(f"/api/v1/assets/{derived_id}/pixel", params={"col": 0, "row": 0}).json()["values"][0]["nodata"]
    lineage = tools(client, {"id": derived_id})["derived_from"]
    assert {(row["role"], row["asset_id"]) for row in lineage} == {("a", a["id"]), ("b", b["id"])}
    assert {row["parent_checksum"] for row in lineage} == {a["sha256"], b["sha256"]}
    assert (
        lineage[0]["parameters"]["formula"] == "B - A"
        and lineage[0]["parameters"]["reason"] == "Before and after the works."
    )
    with get_engine().connect() as conn:
        origin = conn.execute(text("SELECT origin FROM asset WHERE id = :id"), {"id": derived_id}).scalar_one()
    assert origin == "derived"
    with pytest.raises(DBAPIError, match="append-only"), get_engine().begin() as conn:
        conn.execute(text("DELETE FROM asset_lineage WHERE child_asset_id = :id"), {"id": derived_id})
    assert client.get(f"/api/v1/assets/{a['id']}/content").content == tif(before, nodata=0)  # inputs unchanged


@pytest.mark.parametrize(
    ("other", "reason"),
    [
        ({"transform": from_origin(500010, 3000000, 10, 10)}, "x origin"),
        ({"crs": "EPSG:32644"}, "CRSs differ"),
        ({"shape": (60, 81)}, "sizes differ"),
        ({"crs": None}, "no valid georeferencing"),
    ],
)
def test_rasters_on_different_grids_are_not_compared(client, project, headers, run_jobs, other, reason):
    a, _ = put(client, project, headers, run_jobs, tif(gradient(bands=1)), name="a.tif")
    shape = other.pop("shape", None)
    data = gradient(bands=1, height=shape[0], width=shape[1]) if shape else gradient(bands=1) + 1
    b, _ = put(client, project, headers, run_jobs, tif(data, **other), name="b.tif")
    check = client.get(f"/api/v1/assets/{a['id']}/alignment/{b['id']}").json()
    assert check["aligned"] is False and any(reason in r for r in check["reasons"])
    refused = compare(client, project, headers, a, b)
    assert refused.status_code == 422 and refused.json()["code"] == "NOT_ALIGNED"


def test_a_raster_is_not_compared_with_itself_or_its_copy(client, project, headers, run_jobs):
    body = tif(gradient(bands=1))
    a, _ = put(client, project, headers, run_jobs, body, name="a.tif")
    copy, _ = put(client, project, headers, run_jobs, body, name="copy.tif")
    assert "different rasters" in compare(client, project, headers, a, a).json()["detail"]
    assert "identical content" in compare(client, project, headers, a, copy).json()["detail"]
    assert compare(client, project, headers, a, copy, band_a=5).json()["code"] == "BAND_INVALID"


def test_a_comparison_of_rasters_placed_from_recorded_georeferencing_says_so(client, project, headers, run_jobs):
    a, _ = put(client, project, headers, run_jobs, tif(gradient(bands=1), crs=None), name="a.tif")
    b, _ = put(client, project, headers, run_jobs, tif(gradient(bands=1) + 3), name="b.tif")
    client.post(
        f"/api/v1/assets/{a['id']}/georeferencing",
        json={"operation": "assign_crs", "crs_id": "EPSG:32643", "reason": "Same scene series.", "expected_version": 0,
              "confirm": True},
        headers=headers,
    )  # fmt: skip
    assert compare(client, project, headers, a, b).status_code == 202
    run_jobs()
    (done,) = client.get(f"/api/v1/projects/{project['id']}/comparisons").json()
    derived = client.get(f"/api/v1/assets/{done['derived_asset_id']}").json()
    assert "CRS_USER_ASSERTED" in derived["quality_flags"]
    layer = next(r for r in client.get(f"/api/v1/projects/{project['id']}/layers").json()["rasters"]
                 if r["asset_id"] == done["derived_asset_id"])  # fmt: skip
    assert layer["placement"] == "user_asserted"
