"""Discovery of locally stored Sentinel-2 bands for a derived index (geopoc.geo.bandscan, routers/indices.py).

Read-only and offline. The band folder is a temporary directory set through the settings dependency; a client never
supplies a path, and no response carries one."""

import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import Affine

from geopoc.api.main import app
from geopoc.api.routers.imagery import get_imagery_settings
from geopoc.config import Settings
from geopoc.geo import bandscan
from geopoc.geo.indices import INDICES
from tests.test_imagery import db_state
from tests.test_indices import TRANSFORM, product_xml, write_band

SPEC = INDICES["ndvi"]
RED_STEM = "T55HGS_20240115T235221_B04_10m"
NIR_STEM = "T55HGS_20240115T235221_B08_10m"


def make_scene(
    folder: Path, *, red=True, nir=True, nir_transform=TRANSFORM, nir_crs=CRS.from_epsg(32755), xml=None
) -> Path:
    """A scene folder: the product metadata and the band files in the SAFE-like place its references name."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "MTD_MSIL2A.xml").write_bytes(xml or product_xml())
    images = folder / "GRANULE" / "L2A_T55HGS_A044743_20240115T235222" / "IMG_DATA" / "R10m"
    images.mkdir(parents=True, exist_ok=True)
    data = np.full((40, 60), 1500, dtype="uint16")
    if red:
        write_band(images / f"{RED_STEM}.tif", data)
    if nir:
        write_band(images / f"{NIR_STEM}.tif", data, transform=nir_transform, crs=nir_crs)
    return folder


def settings_for(root) -> Settings:
    return Settings(_env_file=None, imagery_band_root=root)  # type: ignore[call-arg]


@pytest.fixture
def api(client, headers, project):
    state = SimpleNamespace(settings=settings_for(None), client=client, headers=headers, project=project)
    app.dependency_overrides[get_imagery_settings] = lambda: state.settings
    yield state
    app.dependency_overrides.pop(get_imagery_settings, None)


def get_inputs(api, index="ndvi", project=None):
    return api.client.get(
        f"/api/v1/projects/{project or api.project['id']}/indices/{index}/inputs", headers=api.headers
    )


# --- the scan ---------------------------------------------------------------------------------------------------------


def test_a_complete_scene_is_found_with_per_band_scaling(tmp_path):
    make_scene(tmp_path / "scene")
    (scene,) = bandscan.scan(tmp_path, SPEC)
    assert scene.compatible and scene.problems == [] and scene.product.tile_id == "T55HGS"
    nir, red = scene.bands  # in the index's own order: (NIR - Red) -> NIR first
    assert (red.role, red.band, red.file_name) == ("red", "B04", f"{RED_STEM}.tif")
    assert (nir.role, nir.band, nir.file_name) == ("nir", "B08", f"{NIR_STEM}.tif")
    assert red.scaling["add_offset"] == -1000.0 and nir.scaling["add_offset"] == -500.0
    assert (red.width, red.height, red.crs, red.pixel_size) == (60, 40, "EPSG:32755", [10.0, 10.0])


def test_a_missing_band_makes_the_scene_incompatible(tmp_path):
    make_scene(tmp_path / "scene", nir=False)
    (scene,) = bandscan.scan(tmp_path, SPEC)
    nir = scene.bands[0]
    assert not scene.compatible and not nir.found and nir.problems and "No local file" in nir.problems[0]


@pytest.mark.parametrize(
    "over",
    [
        {"nir_crs": CRS.from_epsg(32756)},
        {"nir_transform": TRANSFORM * Affine.translation(30, 0)},
        {"nir_transform": Affine(20.0, 0, 699960.0, 0, -20.0, 5700040.0)},
    ],
)
def test_bands_on_different_grids_are_reported_incompatible(tmp_path, over):
    make_scene(tmp_path / "scene", **over)
    (scene,) = bandscan.scan(tmp_path, SPEC)
    assert not scene.compatible and scene.problems and all(b.readable for b in scene.bands)


def test_a_product_without_the_needed_scaling_is_flagged_per_band(tmp_path):
    make_scene(tmp_path / "scene", xml=product_xml(nir_offset=None))
    (scene,) = bandscan.scan(tmp_path, SPEC)
    assert not scene.compatible and scene.bands[0].problems and not scene.bands[0].found


def test_unreadable_band_files_are_reported_not_raised(tmp_path):
    folder = make_scene(tmp_path / "scene")
    (folder / "GRANULE/L2A_T55HGS_A044743_20240115T235222/IMG_DATA/R10m" / f"{RED_STEM}.tif").write_bytes(
        b"not a raster"
    )
    (scene,) = bandscan.scan(tmp_path, SPEC)
    assert not scene.compatible and scene.bands[1].found and not scene.bands[1].readable and scene.bands[1].problems


def test_scenes_are_ordered_newest_first_and_ids_are_stable_and_opaque(tmp_path):
    make_scene(tmp_path / "a")
    older = (
        product_xml()
        .replace(b"2024-01-15T23:52:21.024Z", b"2023-06-01T00:00:00.000Z")
        .replace(b"20240116T021554", b"20230602T000000")
    )
    make_scene(tmp_path / "b", xml=older)
    scenes = bandscan.scan(tmp_path, SPEC)
    assert [s.product.start_time[:4] for s in scenes] == ["2024", "2023"]
    again = bandscan.scan(tmp_path, SPEC)
    assert [s.scene_id for s in again] == [s.scene_id for s in scenes] and len({s.scene_id for s in scenes}) == 2
    assert all(len(s.scene_id) == 16 and "/" not in s.scene_id for s in scenes)
    assert bandscan.find_scene(tmp_path, SPEC, scenes[0].scene_id).scene_id == scenes[0].scene_id
    assert bandscan.find_scene(tmp_path, SPEC, "../../etc/passwd") is None


def test_nothing_is_offered_without_a_folder_or_with_a_bad_one(tmp_path):
    assert bandscan.scan(None, SPEC) == []
    assert bandscan.scan(tmp_path / "does-not-exist", SPEC) == []
    file = tmp_path / "a-file"
    file.write_text("x")
    assert bandscan.scan(file, SPEC) == []


def test_the_walk_is_shallow_and_oversized_metadata_is_ignored(tmp_path):
    deep = tmp_path
    for part in ("1", "2", "3", "4", "5"):
        deep = deep / part
    make_scene(deep)  # five folders down: beyond the depth limit
    assert bandscan.scan(tmp_path, SPEC) == []
    big = tmp_path / "big"
    big.mkdir()
    (big / "MTD_MSIL2A.xml").write_bytes(b"x" * (3 * 1024 * 1024))
    assert bandscan.scan(tmp_path, SPEC) == []


def test_a_linked_band_file_pointing_outside_the_folder_is_not_used(tmp_path):
    outside = tmp_path / "outside"
    make_scene(outside, red=False, nir=False)
    real = write_band(outside / "real.tif", np.full((40, 60), 1500, dtype="uint16"))
    root = tmp_path / "root"
    folder = make_scene(root / "scene", red=False, nir=False)
    images = folder / "GRANULE/L2A_T55HGS_A044743_20240115T235222/IMG_DATA/R10m"
    try:
        (images / f"{RED_STEM}.tif").symlink_to(real)
        (images / f"{NIR_STEM}.tif").symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symbolic links")
    (scene,) = bandscan.scan(root, SPEC)
    assert not scene.compatible and not any(b.found for b in scene.bands)


# --- the API ----------------------------------------------------------------------------------------------------------


def test_the_index_list_describes_ndvi_only(api):
    response = api.client.get("/api/v1/indices", headers=api.headers)
    assert response.status_code == 200
    (ndvi,) = response.json()["indices"]
    assert (ndvi["key"], ndvi["formula"]) == ("ndvi", "(NIR - Red) / (NIR + Red)")
    assert ndvi["inputs"] == [{"role": "nir", "band": "B08"}, {"role": "red", "band": "B04"}]


def test_both_routes_need_a_workspace_profile(client, project):
    assert client.get("/api/v1/indices").status_code == 401
    assert client.get(f"/api/v1/projects/{project['id']}/indices/ndvi/inputs").status_code == 401


def test_unknown_project_and_unknown_index_are_404(api):
    assert get_inputs(api, project=uuid.uuid4()).status_code == 404
    response = get_inputs(api, index="ndwi")
    assert response.status_code == 404 and response.json()["code"] == "INDEX_NOT_FOUND"


def test_without_a_configured_folder_nothing_is_offered(api):
    body = get_inputs(api).json()
    assert body["configured"] is False and body["scenes"] == [] and "No band folder" in body["note"]


def test_configured_scenes_are_listed_without_any_path(api, tmp_path):
    make_scene(tmp_path / "complete")
    make_scene(tmp_path / "broken", nir=False)
    api.settings = settings_for(tmp_path)
    response = get_inputs(api)
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True and len(body["scenes"]) == 2
    assert sorted(s["compatible"] for s in body["scenes"]) == [False, True]
    good = next(s for s in body["scenes"] if s["compatible"])
    assert good["tile_id"] == "T55HGS" and good["acquisition_start"] == "2024-01-15T23:52:21.024Z"
    by_role = {b["role"]: b for b in good["bands"]}
    assert sorted(by_role) == ["nir", "red"] and by_role["red"]["scaling"]["add_offset"] == -1000.0
    assert by_role["nir"]["scaling"]["add_offset"] == -500.0
    raw = response.text
    assert str(tmp_path) not in raw and str(tmp_path).replace("\\", "/") not in raw and "\\\\" not in raw
    assert "complete" not in raw and "GRANULE" not in raw  # neither folder names nor sub-paths


def test_the_discovery_route_writes_nothing(api, tmp_path):
    make_scene(tmp_path / "scene")
    api.settings = settings_for(tmp_path)
    before_db, before_files = db_state(), sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    for _ in range(2):
        assert get_inputs(api).status_code == 200
    assert db_state() == before_db
    assert before_files == sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))


def test_the_routes_accept_no_path_parameters(api):
    schema = app.openapi()["paths"]["/api/v1/projects/{project_id}/indices/{index_key}/inputs"]["get"]
    assert {p["name"].lower() for p in schema["parameters"]} <= {"project_id", "index_key", "x-workspace-profile"}
    assert "requestBody" not in schema


def test_the_index_modules_have_no_network_and_the_engine_has_no_database_access():
    import re

    root = Path(__file__).resolve().parents[1] / "geopoc"

    def code(name):
        lines = (root / name).read_text(encoding="utf-8").splitlines()
        return "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    network = (
        r"(?m)^\s*(?:from|import)\s+(?:httpx|requests|urllib|socket|boto3|ee\b|geopoc\.earth_engine|geopoc\.satellite)"
    )
    for name in ("geo/indices.py", "geo/bandscan.py", "geo/colormaps.py", "derived_index.py", "api/routers/indices.py"):
        assert not re.search(network, code(name)), name
    # The pure engine, the scan and the colour ramps never touch the database (the job module and the router do, through
    # the existing queue and ingestion paths).
    for name in ("geo/indices.py", "geo/bandscan.py", "geo/colormaps.py"):
        assert not re.search(r"session\.|\.commit\(|sqlalchemy|geopoc\.db", code(name)), name
    # The discovery and listing routes only read: the one write path in the router is a queued job, never a file path.
    assert "open(" not in code("api/routers/indices.py") and "write_" not in code("api/routers/indices.py")
