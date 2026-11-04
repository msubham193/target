"""Phase 4: map layers, raster tiles and coordinate conversion.

Rasters are synthetic GeoTIFFs written in memory with a known CRS and geotransform, so placement can be checked
against independently computed positions.
"""

import io
import math

import numpy as np
import pyproj
import pytest
from PIL import Image
from rasterio.transform import Affine

from tests import media
from tests.test_assets import upload

ORIGIN = 20037508.342789244


def raster(client, project, headers, run_jobs, name="scene.tif", **kwargs):
    response = upload(client, project, headers, name, media.geotiff_bytes(**kwargs), modality="overhead_raster")
    assert response.status_code == 201, response.text
    run_jobs()  # preview
    return response.json()["asset"]


def layers(client, project):
    response = client.get(f"/api/v1/projects/{project['id']}/layers")
    assert response.status_code == 200, response.text
    return response.json()


def tile_of(lon: float, lat: float, z: int) -> tuple[int, int, float, float]:
    """XYZ tile containing a point, and the point's pixel position inside that tile."""
    x_m, y_m = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(lon, lat)
    size = 2 * ORIGIN / 2**z
    fx, fy = (x_m + ORIGIN) / size, (ORIGIN - y_m) / size
    return int(fx), int(fy), (fx - int(fx)) * 256, (fy - int(fy)) * 256


def test_only_validly_georeferenced_rasters_are_placed(client, project, headers, run_jobs):
    """T-03 (map half): a raster without a CRS is listed as excluded, never positioned."""
    placed = raster(client, project, headers, run_jobs)
    no_crs = raster(client, project, headers, run_jobs, name="scan.tif", crs=None)
    no_transform = raster(client, project, headers, run_jobs, name="plain.tif", transform=None)
    photo = upload(client, project, headers, "photo.jpg", media.image_bytes("JPEG")).json()["asset"]

    result = layers(client, project)
    (layer,) = result["rasters"]
    assert layer["asset_id"] == placed["id"] and layer["placement"] == "trusted"
    assert layer["crs_id"] == "EPSG:32643"
    assert layer["tile_url"] == f"/api/v1/assets/{placed['id']}/tiles/{{z}}/{{x}}/{{y}}.png?v=0"
    assert layer["georef_version"] == 0
    west, south, east, north = layer["bounds_4326"]
    assert west == pytest.approx(75.0, abs=1e-6) and north == pytest.approx(27.12247, abs=1e-4)
    assert east > west and north > south
    assert layer["footprint_transformation"]["target_crs_id"] == "EPSG:4326"
    assert layer["native_zoom"] == 14  # 10 m pixels at about 27 degrees north

    reasons = {item["asset_id"]: item["reason"] for item in result["excluded"]}
    assert reasons == {
        no_crs["id"]: "CRS_MISSING",
        no_transform["id"]: "NO_GEOTRANSFORM",
        photo["id"]: "NOT_GEOREFERENCED",
    }
    assert result["basemap"]["configured"] is False
    assert result["aoi"] is None
    assert result["observations"] == {
        "with_location": 0,
        "without_location": 0,
        "note": result["observations"]["note"],
    }


def test_project_location_is_a_separate_layer_and_places_nothing(client, headers):
    created = client.post(
        "/api/v1/projects",
        json={
            "name": "With AOI",
            "aoi": {"geometry": {"type": "Point", "coordinates": [500000, 3000000]}, "crs_id": "EPSG:32643"},
        },
        headers=headers,
    ).json()
    photo = upload(client, created, headers, "photo.jpg", media.image_bytes("JPEG")).json()["asset"]
    result = layers(client, created)
    assert result["aoi"]["entered_crs_id"] == "EPSG:32643"
    assert result["aoi"]["geometry_4326"]["coordinates"][0] == pytest.approx(75.0)
    assert "not evidence" in result["aoi"]["note"]
    assert result["rasters"] == [] and [e["asset_id"] for e in result["excluded"]] == [photo["id"]]


def _decode(response) -> np.ndarray:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    image = Image.open(io.BytesIO(response.content))
    assert image.size == (256, 256) and image.mode == "RGBA"
    return np.asarray(image)


def test_tiles_put_the_raster_where_its_own_georeferencing_says(client, project, headers, run_jobs):
    asset = raster(client, project, headers, run_jobs, size=(200, 150))
    corner_lon, corner_lat = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True).transform(
        500000 + 1000, 3000000 - 750
    )  # the centre of the 2 km x 1.5 km raster
    z = 15
    x, y, px, py = tile_of(corner_lon, corner_lat, z)
    pixels = _decode(client.get(f"/api/v1/assets/{asset['id']}/tiles/{z}/{x}/{y}.png"))
    assert pixels[int(py), int(px), 3] == 255  # the raster's centre is opaque in the tile that contains it

    # The raster's north-west corner (500000, 3000000): just inside it is opaque, just outside it is transparent.
    lon, lat = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True).transform(500000, 3000000)
    x, y, px, py = tile_of(lon, lat, 17)
    tile = _decode(client.get(f"/api/v1/assets/{asset['id']}/tiles/17/{x}/{y}.png"))
    inside = tile[min(255, int(py) + 4), min(255, int(px) + 4), 3]
    outside = tile[max(0, int(py) - 4), max(0, int(px) - 4), 3]
    assert (inside, outside) == (255, 0)

    # Far away: an empty tile, not an error and not a picture.
    far_x, far_y, _, _ = tile_of(-100.0, 40.0, z)
    assert client.get(f"/api/v1/assets/{asset['id']}/tiles/{z}/{far_x}/{far_y}.png").status_code == 204


def test_rotated_raster_tiles_follow_the_full_affine_transform(client, project, headers, run_jobs):
    """Rotation is applied, not assumed away: the rotated outline differs from the north-up bounding box."""
    angle = math.radians(30)
    rotated = Affine(
        10 * math.cos(angle), -10 * math.sin(angle), 500000, -10 * math.sin(angle), -10 * math.cos(angle), 3000000
    )
    asset = raster(client, project, headers, run_jobs, transform=rotated, size=(200, 200))
    to_lonlat = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    # A point inside the rotated raster: pixel (100, 100) through the affine transform.
    inside = to_lonlat.transform(*(rotated @ (100, 100)))
    # A point inside the north-up bounding box of the footprint but outside the rotated raster.
    corners = [rotated @ c for c in ((0, 0), (200, 0), (200, 200), (0, 200))]
    xs, ys = zip(*corners, strict=True)
    outside = to_lonlat.transform(min(xs) + 30, max(ys) - 30)
    for (lon, lat), expected in ((inside, 255), (outside, 0)):
        x, y, px, py = tile_of(lon, lat, 15)
        tile = _decode_or_empty(client.get(f"/api/v1/assets/{asset['id']}/tiles/15/{x}/{y}.png"))
        assert tile[int(py), int(px), 3] == expected


def _decode_or_empty(response) -> np.ndarray:
    if response.status_code == 204:
        return np.zeros((256, 256, 4), dtype="uint8")
    return _decode(response)


def test_tiles_are_refused_for_rasters_that_cannot_be_placed(client, project, headers, run_jobs):
    no_crs = raster(client, project, headers, run_jobs, name="scan.tif", crs=None)
    refused = client.get(f"/api/v1/assets/{no_crs['id']}/tiles/10/0/0.png")
    assert refused.status_code == 409 and refused.json()["code"] == "CRS_UNRESOLVED"
    placed = raster(client, project, headers, run_jobs)
    assert client.get(f"/api/v1/assets/{placed['id']}/tiles/30/0/0.png").status_code == 422
    assert client.get(f"/api/v1/assets/{placed['id']}/tiles/2/4/0.png").status_code == 404
    photo = upload(client, project, headers, "photo.jpg", media.image_bytes("JPEG")).json()["asset"]
    assert client.get(f"/api/v1/assets/{photo['id']}/tiles/1/0/0.png").json()["code"] == "CRS_UNRESOLVED"


def test_the_original_is_unchanged_by_tiling(client, project, headers, run_jobs):
    body = media.geotiff_bytes()
    asset = upload(client, project, headers, "scene.tif", body, modality="overhead_raster").json()["asset"]
    run_jobs()
    lon, lat = 75.0005, 27.1222
    x, y, _, _ = tile_of(lon, lat, 15)
    client.get(f"/api/v1/assets/{asset['id']}/tiles/15/{x}/{y}.png")
    assert client.get(f"/api/v1/assets/{asset['id']}/content").content == body


# --- coordinate conversion (GEO-002, PT-17) ------------------------------------------------------------------------------


def test_point_conversion_round_trips_and_keeps_x_first(client):
    forward = client.get(
        "/api/v1/crs/transform", params={"source": "EPSG:32643", "target": "EPSG:4326", "x": 500000, "y": 3000000}
    ).json()
    # EPSG:4326's authority order is latitude first; the API is always x (longitude) first.
    assert forward["x"] == pytest.approx(75.0, abs=1e-9)
    assert forward["y"] == pytest.approx(27.12247, abs=1e-5)
    assert [a["direction"] for a in forward["target_axes"]] == ["north", "east"]
    assert "x is easting or longitude" in forward["order_note"]
    assert forward["transformation"]["network"] == "disabled"
    back = client.get(
        "/api/v1/crs/transform",
        params={"source": "EPSG:4326", "target": "EPSG:32643", "x": forward["x"], "y": forward["y"]},
    ).json()
    assert back["x"] == pytest.approx(500000, abs=1e-4) and back["y"] == pytest.approx(3000000, abs=1e-4)
    assert back["warnings"] == []


def test_point_conversion_refuses_incomplete_crs_and_flags_area_of_use(client):
    incomplete = client.get(
        "/api/v1/crs/transform", params={"source": "WGS84 + LCC", "target": "EPSG:4326", "x": 1, "y": 2}
    )
    assert incomplete.status_code == 422 and incomplete.json()["code"] == "CRS_UNRESOLVED"
    far = client.get(
        "/api/v1/crs/transform", params={"source": "EPSG:4326", "target": "EPSG:32643", "x": -100, "y": 40}
    ).json()
    assert "OUTSIDE_AREA_OF_USE" in far["warnings"]
    vertical = client.get(
        "/api/v1/crs/transform", params={"source": "EPSG:4326", "target": "EPSG:5773", "x": 1, "y": 2}
    )
    assert vertical.status_code == 422 and vertical.json()["code"] == "CRS_UNSUITABLE"
