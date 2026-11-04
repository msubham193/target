"""NDVI as a background job (geopoc.derived_index, routers/indices.py): starting a run, the job lifecycle, the stored
derived asset and its provenance, failures and cancellation, project isolation, and the tile colour ramp.

Synthetic Sentinel-2-like bands in a temporary folder; the real job handler runs in this process through the same queue
and runner the worker uses. Nothing touches the network, and the band folder is never the developer's real one."""

import io
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from PIL import Image
from pyproj import Transformer
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from geopoc import derived_index
from geopoc.api.main import app
from geopoc.api.routers.imagery import get_imagery_settings
from geopoc.config import Settings
from geopoc.db.models import Asset, Blob, Job
from geopoc.db.session import get_engine
from geopoc.geo import colormaps, tiles
from geopoc.geo.indices import INDICES
from geopoc.jobs.handlers import JobContext
from geopoc.storage import store
from tests.test_indices import TRANSFORM, expected_ndvi, product_xml, sha, write_band

SPEC = INDICES["ndvi"]
RED_STEM = "T55HGS_20240115T235221_B04_10m"
NIR_STEM = "T55HGS_20240115T235221_B08_10m"
HEIGHT, WIDTH = 160, 240


def band_arrays():
    """Left third: NDVI below zero. Middle: moderate. Right: high. A nodata block, and one value that is out of range."""
    red = np.empty((HEIGHT, WIDTH), dtype="uint16")
    nir = np.empty((HEIGHT, WIDTH), dtype="uint16")
    red[:, :80], nir[:, :80] = 2000, 1040  # red 0.10, nir 0.054 -> about -0.30
    red[:, 80:160], nir[:, 80:160] = 1500, 3000  # red 0.05, nir 0.25 -> about 0.67
    red[:, 160:], nir[:, 160:] = 1200, 5500  # red 0.02, nir 0.50 -> about 0.92
    red[10:60, 10:50], nir[10:60, 10:50] = 0, 0  # product NODATA
    red[100, 120], nir[100, 120] = 900, 700  # red -0.01, nir 0.02 -> +3.0: out of range
    return red, nir


def build_scene(folder: Path, red=None, nir=None, xml=None, transform=TRANSFORM) -> Path:
    base_red, base_nir = band_arrays()
    red = base_red if red is None else red
    nir = base_nir if nir is None else nir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "MTD_MSIL2A.xml").write_bytes(xml or product_xml())
    images = folder / "GRANULE" / "L2A_T55HGS_A044743_20240115T235222" / "IMG_DATA" / "R10m"
    images.mkdir(parents=True, exist_ok=True)
    write_band(images / f"{RED_STEM}.tif", red)
    write_band(images / f"{NIR_STEM}.tif", nir, transform=transform)
    return images


def settings_for(root) -> Settings:
    return Settings(_env_file=None, imagery_band_root=root)  # type: ignore[call-arg]


@pytest.fixture
def band_root(tmp_path):
    root = tmp_path / "bands"
    build_scene(root / "scene")
    return root


@pytest.fixture
def api(client, headers, project, band_root, monkeypatch):
    """The real routes and the real job handler, both looking at this temporary band folder."""
    state = SimpleNamespace(client=client, headers=headers, project=project, root=band_root)
    app.dependency_overrides[get_imagery_settings] = lambda: settings_for(state.root)
    monkeypatch.setattr(derived_index, "get_settings", lambda: SimpleNamespace(imagery_band_root=state.root))
    yield state
    app.dependency_overrides.pop(get_imagery_settings, None)


def scene_id(api) -> str:
    response = api.client.get(f"/api/v1/projects/{api.project['id']}/indices/ndvi/inputs", headers=api.headers)
    (scene,) = [s for s in response.json()["scenes"] if s["compatible"]]
    return scene["scene_id"]


def runs_url(api, project_id=None, index="ndvi") -> str:
    return f"/api/v1/projects/{project_id or api.project['id']}/indices/{index}/runs"


def start(api, sid=None, **kwargs):
    return api.client.post(runs_url(api), json={"scene_id": sid or scene_id(api)}, headers=api.headers, **kwargs)


def get_run(api, job_id, project_id=None):
    return api.client.get(f"{runs_url(api, project_id)}/{job_id}", headers=api.headers)


def finish_all(run_jobs) -> list[str]:
    return run_jobs()


def only_run(api) -> dict:
    return api.client.get(runs_url(api), headers=api.headers).json()["runs"][0]


# --- starting a run ---------------------------------------------------------------------------------------------------------


def test_starting_a_run_needs_a_profile(api):
    sid = scene_id(api)
    response = api.client.post(runs_url(api), json={"scene_id": sid})
    assert response.status_code == 401


def test_unknown_project_index_and_scene_are_404(api):
    sid = scene_id(api)
    unknown_project = "00000000-0000-4000-8000-0000000000aa"
    assert (
        api.client.post(runs_url(api, unknown_project), json={"scene_id": sid}, headers=api.headers).status_code == 404
    )
    response = api.client.post(runs_url(api, index="ndwi"), json={"scene_id": sid}, headers=api.headers)
    assert response.status_code == 404 and response.json()["code"] == "INDEX_NOT_FOUND"
    response = start(api, "0" * 16)
    assert response.status_code == 404 and response.json()["code"] == "SCENE_NOT_FOUND"


@pytest.mark.parametrize(
    "bad", ["../../etc", "C:\\\\Windows", "ABCDEF0123456789", "0123456789abcde", "0" * 17, "", "a b c d e f g h"]
)
def test_a_scene_id_is_an_opaque_token_never_a_path(api, bad):
    response = api.client.post(runs_url(api), json={"scene_id": bad}, headers=api.headers)
    assert response.status_code == 422


def test_no_other_field_is_accepted(api):
    response = api.client.post(
        runs_url(api), json={"scene_id": scene_id(api), "path": "C:/x.tif", "url": "http://x"}, headers=api.headers
    )
    assert response.status_code == 422


def test_bands_that_cannot_be_combined_are_refused_before_a_job_exists(api, tmp_path):
    root = tmp_path / "other"
    build_scene(root / "s", transform=TRANSFORM * __import__("rasterio").transform.Affine.translation(20, 0))
    api.root = root
    response = api.client.get(f"/api/v1/projects/{api.project['id']}/indices/ndvi/inputs", headers=api.headers)
    (scene,) = response.json()["scenes"]
    assert scene["compatible"] is False and scene["problems"]
    started = start(api, scene["scene_id"])
    assert started.status_code == 422 and started.json()["code"] == "INPUTS_INCOMPATIBLE"
    with Session(get_engine()) as session:
        assert session.scalars(select(Job).where(Job.kind == "derived_index")).first() is None


def test_an_archived_project_is_refused(api):
    sid = scene_id(api)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE project SET archived_at = now()"))
    response = start(api, sid)
    assert response.status_code == 409 and response.json()["code"] == "PROJECT_ARCHIVED"


def test_a_started_run_is_a_queued_derived_index_job_without_any_path(api):
    response = start(api)
    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["status"], body["index"], body["range_mask"]) == ("queued", "ndvi", [-1.0, 1.0])
    assert [i["role"] for i in body["inputs"]] == ["nir", "red"] and body["result"] is None
    assert {i["file_name"] for i in body["inputs"]} == {f"{NIR_STEM}.tif", f"{RED_STEM}.tif"}
    with Session(get_engine()) as session:
        job = session.get(Job, body["job_id"])
        assert job.kind == "derived_index" and str(job.project_id) == api.project["id"] and job.asset_id is None
        assert job.payload["scene_id"] == body["scene_id"] and job.created_by is not None
    assert str(api.root) not in response.text and str(api.root).replace("\\", "/") not in response.text
    legend = body["display"]
    assert legend["colormap"] == "ndvi" and legend["low"] < 0 < legend["high"] and len(legend["stops"]) >= 5


def test_a_second_run_of_the_same_scene_is_refused_while_one_is_active(api):
    first = start(api).json()
    again = start(api)
    assert again.status_code == 409 and again.json()["code"] == "RUN_ALREADY_ACTIVE"
    assert first["job_id"] in again.json()["detail"]
    cancelled = api.client.post(f"{runs_url(api)}/{first['job_id']}/cancel", headers=api.headers)
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
    assert start(api).status_code == 202  # allowed again once the first is over


def test_runs_are_listed_newest_first_and_fetched_one_by_one(api):
    one = start(api).json()
    api.client.post(f"{runs_url(api)}/{one['job_id']}/cancel", headers=api.headers)
    two = start(api).json()
    runs = api.client.get(runs_url(api), headers=api.headers).json()["runs"]
    assert [r["job_id"] for r in runs] == [two["job_id"], one["job_id"]]
    assert get_run(api, two["job_id"]).json()["job_id"] == two["job_id"]
    assert get_run(api, "00000000-0000-4000-8000-0000000000bb").status_code == 404


def test_runs_do_not_leak_between_projects_or_indices(api):
    started = start(api).json()
    other = api.client.post("/api/v1/projects", json={"name": "Other"}, headers=api.headers).json()
    assert get_run(api, started["job_id"], other["id"]).status_code == 404
    assert api.client.get(runs_url(api, other["id"]), headers=api.headers).json()["runs"] == []
    assert api.client.get(f"{runs_url(api, index='ndwi')}", headers=api.headers).status_code == 404
    cancel = api.client.post(f"{runs_url(api, other['id'])}/{started['job_id']}/cancel", headers=api.headers)
    assert cancel.status_code == 404
    assert get_run(api, started["job_id"]).json()["status"] == "queued"  # the other project could not touch it


# --- the job ------------------------------------------------------------------------------------------------------------------


def run_to_completion(api, run_jobs):
    started = start(api).json()
    statuses = run_jobs()
    run = get_run(api, started["job_id"]).json()
    return started, statuses, run


def stored_array(asset_id) -> np.ndarray:
    with Session(get_engine()) as session:
        asset = session.get(Asset, asset_id)
        blob = session.get(Blob, asset.blob_sha256)
        path = store.absolute_path(blob.storage_path)
    with rasterio.open(path) as src:
        return src.read(1)


def expected_final():
    red, nir = band_arrays()
    ndvi, r, n = expected_ndvi(red, nir)
    keep = (red != 0) & (nir != 0) & (np.abs(r + n) >= 1e-6)
    stored = np.where(keep, ndvi, np.nan).astype("float32")
    keep &= (stored >= -1.0) & (stored <= 1.0)
    return np.where(keep, stored, np.float32(-9999.0)), ndvi, keep


def test_the_job_completes_with_real_progress_and_a_result(api, run_jobs, monkeypatch):
    seen = []
    original = JobContext.progress
    monkeypatch.setattr(derived_index.indices, "ROWS_PER_BLOCK", 32)  # five strips

    def recording(self, fraction):
        seen.append(round(fraction, 3))
        return original(self, fraction)

    monkeypatch.setattr(JobContext, "progress", recording)
    started, statuses, run = run_to_completion(api, run_jobs)
    assert "failed" not in statuses and run["status"] == "completed", run
    assert run["progress"] == 1.0 and run["error"] is None and run["finished_at"] is not None
    work = [f for f in seen if 0.02 < f < 0.8]
    assert len(work) >= 5 and work == sorted(work)  # one report per strip of real work, never faked
    assert run["asset_id"] == run["result"]["derived_asset_id"]


def test_the_result_carries_statistics_counts_inputs_and_provenance(api, run_jobs):
    _, _, run = run_to_completion(api, run_jobs)
    result = run["result"]
    final, ndvi, keep = expected_final()
    valid = final[final != -9999.0]
    assert (
        result["crs"] == "EPSG:32755"
        and result["resolution_m"] == 10.0
        and (result["width"], result["height"]) == (WIDTH, HEIGHT)
    )
    assert result["nodata"] == -9999.0 and result["range_mask"] == [-1.0, 1.0]
    counts = result["counts"]
    assert counts["valid"] == int(keep.sum()) == valid.size
    assert counts["masked_out_of_range"] == 1 and counts["input_nodata"] == 50 * 40
    assert counts["valid_before_range_mask"] == counts["valid"] + 1
    assert counts["valid"] + counts["invalid"] == counts["pixels"] == WIDTH * HEIGHT
    stats = result["statistics"]
    assert stats["min"] == pytest.approx(float(valid.min()), abs=1e-6) and stats["max"] == pytest.approx(
        float(valid.max()), abs=1e-6
    )
    assert stats["mean"] == pytest.approx(float(valid.astype("float64").mean()), abs=1e-6)
    assert -1.0 <= stats["min"] < 0 < stats["max"] <= 1.0 and set(stats["percentiles"]) == {
        "1",
        "5",
        "25",
        "50",
        "75",
        "95",
        "99",
    }
    assert result["validation"]["failures"] == [] and result["validation"]["strip_mismatches"] == 0
    by_role = {i["role"]: i for i in result["inputs"]}
    assert by_role["red"]["sha256"] == sha(
        api.root / "scene" / "GRANULE" / "L2A_T55HGS_A044743_20240115T235222" / "IMG_DATA" / "R10m" / f"{RED_STEM}.tif"
    )
    assert by_role["red"]["scaling"]["add_offset"] == -1000.0 and by_role["nir"]["scaling"]["add_offset"] == -500.0
    assert (
        result["product"]["tile_id"] == "T55HGS"
        and result["product"]["acquisition_start"] == "2024-01-15T23:52:21.024Z"
    )


def test_the_stored_raster_has_the_right_values_and_is_a_derived_asset(api, run_jobs):
    started, _, run = run_to_completion(api, run_jobs)
    asset_id = run["result"]["derived_asset_id"]
    asset = api.client.get(f"/api/v1/assets/{asset_id}", headers=api.headers).json()
    assert asset["modality"] == "overhead_raster" and asset["project_id"] == api.project["id"]
    assert (
        "DERIVED_ASSET" in asset["quality_flags"]
        and asset["original_filename"] == "T55HGS_20240115T235221_NDVI_10m.tif"
    )
    assert asset["acquisition_time"].startswith("2024-01-15T23:52:21")
    assert asset["raster"]["crs_identifier"] == "EPSG:32755" and asset["raster"]["dtypes"] == ["float32"]
    assert asset["raster"]["nodata"] == [-9999.0] and asset["raster"]["georeferenced"] is True
    assert asset["raster"]["pixel_size_x"] == 10.0 and asset["raster"]["geotransform"][:2] == [699960.0, 10.0]
    provenance = asset["metadata"]["derived_index"]
    assert provenance["job_id"] == started["job_id"] and provenance["formula"] == "(NIR - Red) / (NIR + Red)"
    assert [i["file_name"] for i in provenance["inputs"]] == [f"{NIR_STEM}.tif", f"{RED_STEM}.tif"]
    assert provenance["counts"]["masked_out_of_range"] == 1
    with Session(get_engine()) as session:
        job = session.get(Job, started["job_id"])
        assert str(job.asset_id) == asset_id and session.get(Asset, asset_id).origin == "derived"
        assert session.get(Asset, asset_id).blob_sha256 == run["result"]["output_sha256"]
    final, _, _ = expected_final()
    stored = stored_array(asset_id)
    assert np.allclose(stored[final != -9999.0], final[final != -9999.0], atol=1e-6)
    assert (stored[final == -9999.0] == -9999.0).all()
    assert stored[100, 120] == -9999.0  # the +3.0 pixel is masked, not clamped to 1.0
    assert (stored[:, :80][final[:, :80] != -9999.0] < 0).all()  # negative values inside the range are preserved


def test_the_source_bands_are_unchanged_and_no_temporary_file_remains(api, run_jobs):
    images = api.root / "scene" / "GRANULE" / "L2A_T55HGS_A044743_20240115T235222" / "IMG_DATA" / "R10m"
    before = {p.name: sha(p) for p in images.iterdir()} | {"xml": sha(api.root / "scene" / "MTD_MSIL2A.xml")}
    run_to_completion(api, run_jobs)
    assert before == {p.name: sha(p) for p in images.iterdir()} | {"xml": sha(api.root / "scene" / "MTD_MSIL2A.xml")}
    assert not list(store.uploads_dir().glob("*"))  # the working copy was moved into storage or removed


def test_the_original_jp2_style_inputs_are_not_registered_as_assets(api, run_jobs):
    run_to_completion(api, run_jobs)
    with Session(get_engine()) as session:
        names = [a.original_filename for a in session.scalars(select(Asset))]
    assert names == ["T55HGS_20240115T235221_NDVI_10m.tif"]


def test_running_the_same_scene_again_is_allowed_and_links_to_the_identical_earlier_result(api, run_jobs):
    _, _, first = run_to_completion(api, run_jobs)
    _, _, second = run_to_completion(api, run_jobs)
    assert second["status"] == "completed"
    assert second["result"]["duplicate_of_asset_id"] == first["result"]["derived_asset_id"]
    assert second["result"]["output_sha256"] == first["result"]["output_sha256"]  # the calculation is deterministic


def test_a_scene_that_disappears_before_the_job_runs_fails_clearly(api, run_jobs):
    import shutil

    started = start(api).json()
    shutil.rmtree(api.root / "scene")
    run_jobs()
    run = get_run(api, started["job_id"]).json()
    assert run["status"] == "failed" and run["error"]["code"] == "SCENE_NOT_FOUND" and run["result"] is None
    with Session(get_engine()) as session:
        assert session.scalars(select(Asset)).first() is None


def test_bands_that_stop_matching_before_the_job_runs_fail_with_the_reason(api, run_jobs):
    started = start(api).json()
    images = api.root / "scene" / "GRANULE" / "L2A_T55HGS_A044743_20240115T235222" / "IMG_DATA" / "R10m"
    write_band(images / f"{NIR_STEM}.tif", band_arrays()[1][:-8, :])  # a different size now
    run_jobs()
    run = get_run(api, started["job_id"]).json()
    assert run["status"] == "failed" and run["error"]["code"] == "INPUTS_INCOMPATIBLE"
    assert "sizes differ" in run["error"]["message"]


def test_a_run_cancelled_before_it_starts_creates_nothing(api, run_jobs):
    started = start(api).json()
    api.client.post(f"{runs_url(api)}/{started['job_id']}/cancel", headers=api.headers)
    assert run_jobs() == []
    assert get_run(api, started["job_id"]).json()["status"] == "cancelled"
    with Session(get_engine()) as session:
        assert session.scalars(select(Asset)).first() is None


def test_a_run_cancelled_while_processing_stops_and_keeps_nothing(api, run_jobs, monkeypatch):
    monkeypatch.setattr(derived_index.indices, "ROWS_PER_BLOCK", 16)
    started = start(api).json()
    calls = {"n": 0}
    original = JobContext.progress

    def cancelling(self, fraction):
        calls["n"] += 1
        if calls["n"] == 3:  # part-way through the strips
            with get_engine().begin() as conn:
                conn.execute(text("UPDATE job SET cancel_requested = true WHERE id = :id"), {"id": str(self.job.id)})
        return original(self, fraction)

    monkeypatch.setattr(JobContext, "progress", cancelling)
    assert run_jobs() == ["cancelled"]
    run = get_run(api, started["job_id"]).json()
    assert run["status"] == "cancelled" and run["result"] is None and run["asset_id"] is None
    with Session(get_engine()) as session:
        assert session.scalars(select(Asset)).first() is None
    assert not list(store.uploads_dir().glob("*"))  # the partly written raster was removed


def test_cancelling_a_finished_run_is_refused(api, run_jobs):
    _, _, run = run_to_completion(api, run_jobs)
    response = api.client.post(f"{runs_url(api)}/{run['job_id']}/cancel", headers=api.headers)
    assert response.status_code == 409


def test_the_job_kind_is_known_to_the_worker():
    from geopoc.jobs.handlers import HANDLERS

    assert "derived_index" in HANDLERS


def test_the_output_name_comes_from_the_products_own_tile_and_time():
    info = derived_index.indices.read_product(product_xml())
    assert derived_index.output_filename(info, SPEC) == "T55HGS_20240115T235221_NDVI_10m.tif"


# --- the map: a colour ramp over the stored values -----------------------------------------------------------------------------


def tile_at(api, asset_id, col, row, **query):
    """The native-zoom tile that holds source pixel (col, row), and that pixel's position inside the tile."""
    layers = api.client.get(f"/api/v1/projects/{api.project['id']}/layers", headers=api.headers).json()
    layer = next(r for r in layers["rasters"] if r["asset_id"] == asset_id)
    z = layer["native_zoom"]
    easting, northing = TRANSFORM.c + (col + 0.5) * TRANSFORM.a, TRANSFORM.f + (row + 0.5) * TRANSFORM.e
    mx, my = Transformer.from_crs("EPSG:32755", "EPSG:3857", always_xy=True).transform(easting, northing)
    n = 2**z
    size = 2 * tiles.ORIGIN / n
    x, y = int((mx + tiles.ORIGIN) / size), int((tiles.ORIGIN - my) / size)
    left, bottom, right, top = tiles.tile_bounds(z, x, y)
    px, py = int((mx - left) / (right - left) * 256), int((top - my) / (top - bottom) * 256)
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    response = api.client.get(f"/api/v1/assets/{asset_id}/tiles/{z}/{x}/{y}.png" + (f"?{qs}" if qs else ""))
    return response, px, py


def tile_any(api, asset_id, **query):
    """The tile that holds the middle of the raster (for requests whose pixels do not matter)."""
    return tile_at(api, asset_id, WIDTH // 2, HEIGHT // 2, **query)[0]


def image_of(response) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(response.content)).convert("RGBA"))


@pytest.fixture
def finished(api, run_jobs):
    _, _, run = run_to_completion(api, run_jobs)
    return run["result"]["derived_asset_id"]


def test_the_colour_ramp_draws_values_through_the_ramp_and_leaves_nodata_transparent(api, finished):
    low, high = colormaps.get("ndvi").default_range
    table = colormaps.lut("ndvi", low, high)
    final, _, _ = expected_final()
    for col, row in ((40, 120), (120, 130), (200, 40)):  # the left, middle and right thirds, away from any edge
        response, px, py = tile_at(api, finished, col, row, colormap="ndvi")
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        image = image_of(response)
        assert image.shape == (256, 256, 4)
        value = float(final[row, col])
        index = int(np.clip((value - low) / (high - low), 0, 1) * 255)
        assert tuple(image[py, px, :3]) == tuple(table[index]), (col, row, value)
        assert image[py, px, 3] == 255
    response, px, py = tile_at(api, finished, 30, 35, colormap="ndvi")  # the middle of the product-nodata block
    assert image_of(response)[py, px, 3] == 0
    response, px, py = tile_at(
        api, finished, 100, 100, colormap="ndvi"
    )  # the single masked (+3.0) pixel's surroundings
    assert image_of(response)[py, px, 3] == 255


def test_the_three_regions_get_three_different_colours_in_vegetation_order(api, finished):
    colours = []
    for col in (40, 120, 200):
        response, px, py = tile_at(api, finished, col, 130, colormap="ndvi")
        colours.append(tuple(int(c) for c in image_of(response)[py, px, :3]))
    assert len(set(colours)) == 3
    assert colours[2][1] > colours[2][0] and colours[2][1] > colours[2][2]  # the highest values are green
    assert colours[0][2] > colours[0][0]  # negative values are bluish


def test_the_display_range_changes_the_colours_but_not_the_stored_values(api, finished):
    before = stored_array(finished).copy()
    default = image_of(tile_any(api, finished, colormap="ndvi"))
    wide = image_of(tile_any(api, finished, colormap="ndvi", colormap_range="-1,1"))
    assert not np.array_equal(default, wide)
    assert np.array_equal(stored_array(finished), before)


def test_a_colour_ramp_request_is_validated(api, finished):
    for query in (
        {"colormap": "rainbow"},
        {"colormap": "ndvi", "colormap_range": "1,-1"},
        {"colormap": "ndvi", "colormap_range": "a,b"},
        {"colormap": "ndvi", "colormap_range": "0,nan"},
        {"colormap": "ndvi", "colormap_range": "0,1,2"},
        {"colormap": "ndvi", "colormap_range": "-1e9,1e9"},
    ):
        response = tile_any(api, finished, **query)
        assert response.status_code == 422 and response.json()["code"] == "COLORMAP_INVALID", query


def test_a_colour_ramp_is_refused_for_rasters_that_are_not_single_band_float(api, client, headers, project):
    from tests import media

    upload = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        params={"modality": "overhead_raster", "filename": "plain.tif"},
        content=media.geotiff_bytes(crs="EPSG:32755", transform=TRANSFORM),
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["asset"]["id"]
    response = client.get(f"/api/v1/assets/{asset_id}/tiles/14/0/0.png?colormap=ndvi")
    assert response.status_code == 422 and response.json()["code"] == "COLORMAP_NOT_APPLICABLE"


def test_a_tile_for_an_unknown_asset_is_still_404_with_a_colour_ramp(client):
    response = client.get("/api/v1/assets/00000000-0000-4000-8000-0000000000cc/tiles/3/1/1.png?colormap=ndvi")
    assert response.status_code == 404


def test_without_a_colour_ramp_other_rasters_are_drawn_as_before(tmp_path):
    """Grey for one band and the stretch per channel for three: the default path is unchanged by the extension."""
    one = tmp_path / "one.tif"
    data = np.tile(np.arange(0, 250, dtype="uint8"), (60, 1))[:, :200]
    with rasterio.open(
        one, "w", driver="GTiff", width=200, height=60, count=1, dtype="uint8", crs="EPSG:32755", transform=TRANSFORM
    ) as dst:
        dst.write(data, 1)
    z = 15
    lon, lat = Transformer.from_crs("EPSG:32755", "EPSG:4326", always_xy=True).transform(
        TRANSFORM.c + 1000, TRANSFORM.f - 300
    )
    n = 2**z
    x, y = int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    png = tiles.render_tile(one, (1,), ((0.0, 249.0),), z, x, y)
    assert png is not None
    image = np.array(Image.open(io.BytesIO(png)).convert("RGBA"))
    opaque = image[image[:, :, 3] == 255]
    assert len(opaque) and (opaque[:, 0] == opaque[:, 1]).all() and (opaque[:, 1] == opaque[:, 2]).all()
    ramp = tiles.render_tile(one, (1,), ((0.0, 249.0),), z, x, y, colormap="ndvi")
    assert ramp is not None and ramp != png


# --- the colour ramps themselves ----------------------------------------------------------------------------------------------


def test_the_ramp_table_matches_its_stops_and_blends_between_them():
    ramp = colormaps.get("ndvi")
    low, high = ramp.default_range
    table = colormaps.lut("ndvi", low, high)
    assert table.shape == (256, 3) and table.dtype == np.uint8
    assert tuple(table[0]) == (0x3B, 0x5B, 0x92) and tuple(table[255]) == (0x0B, 0x5D, 0x2A)  # the first and last stops
    assert not np.array_equal(table[100], table[101]) or not np.array_equal(table[101], table[102])
    sand = int(round((0.0 - low) / (high - low) * 255))
    assert tuple(table[sand]) == pytest.approx((0xE8, 0xDF, 0xC8), abs=3)  # zero is drawn as sand
    wide = colormaps.lut("ndvi", -1.0, 1.0)
    assert not np.array_equal(table, wide) and tuple(wide[0]) == tuple(table[0])  # held beyond the first stop


def test_ramp_ranges_are_parsed_strictly():
    assert colormaps.parse_range(None, (-0.2, 0.9)) == (-0.2, 0.9)
    assert colormaps.parse_range("-1,1", (0, 1)) == (-1.0, 1.0)
    for bad in ("1,-1", "1,1", "x", "1", "1,2,3", "nan,1", "inf,2", "-5000,5000"):
        with pytest.raises(colormaps.ColormapError):
            colormaps.parse_range(bad, (0, 1))
    with pytest.raises(colormaps.ColormapError):
        colormaps.get("nope")


def test_the_legend_description_uses_the_same_stops_as_the_tiles():
    legend = colormaps.describe("ndvi")
    assert [s["value"] for s in legend["stops"]] == [v for v, _ in colormaps.get("ndvi").stops]
    assert legend["low"] < 0 < legend["high"] and json.dumps(legend)


def test_the_index_registry_exposes_the_range_and_the_legend(api):
    (ndvi,) = api.client.get("/api/v1/indices", headers=api.headers).json()["indices"]
    assert ndvi["analytical_range"] == [-1.0, 1.0] and ndvi["display"]["colormap"] == "ndvi"
