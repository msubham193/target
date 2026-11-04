"""Map-sheet and satellite overlay: alignment inspection and an actual pixel-level overlay check.

Synthetic rasters. The pixel check draws the SAME ground rectangle from two rasters in different CRSs (UTM and WGS 84
longitude/latitude) and requires the covered area to coincide in the map tiles to within a pixel or two. That shows the
tile pipeline puts the same ground at the same place; it says nothing about the accuracy of any real sheet's georeferencing.
"""

import io

import numpy as np
import pytest
from PIL import Image
from pyproj import Transformer
from rasterio.transform import from_bounds, from_origin

from tests import media
from tests.test_crs_assessment import DELHI, geotiff_near
from tests.test_georeferencing import tile_of
from tests.test_map_sheets import put

UTM = "EPSG:32643"
WGS84 = "EPSG:4326"


def align(client, project, first, second):
    return client.get(
        f"/api/v1/projects/{project['id']}/alignment", params={"first": first["id"], "second": second["id"]}
    )


def lonlat_box(x0, y0, width_m, height_m):
    to = Transformer.from_crs(UTM, WGS84, always_xy=True)
    corners = [to.transform(x0 + dx, y0 + dy) for dx in (0, width_m) for dy in (0, -height_m)]
    return (
        min(c[0] for c in corners),
        min(c[1] for c in corners),
        max(c[0] for c in corners),
        max(c[1] for c in corners),
    )


# --- inspection -------------------------------------------------------------------------------------------------------


def test_a_map_sheet_over_a_satellite_image_is_inspected(client, project, headers, run_jobs):
    sheet = put(
        client, project, headers, run_jobs, "sheet.tif",
        media.geotiff_bytes(crs=UTM, transform=from_origin(500000, 3000000, 10, 10), size=(100, 100)), series="osm",
    )["asset"]  # fmt: skip
    west, south, east, north = lonlat_box(499000, 3001000, 3000, 3000)
    satellite = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        params={"modality": "overhead_raster", "filename": "sat.tif"},
        content=media.geotiff_bytes(crs=WGS84, transform=from_bounds(west, south, east, north, 60, 60), size=(60, 60)),
        headers=headers,
    ).json()["asset"]
    run_jobs()

    response = align(client, project, sheet, satellite)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["overlap"] is True
    assert result["overlap_fraction_of_first"] == pytest.approx(1.0, abs=0.01)
    assert result["overlap_fraction_of_second"] == pytest.approx(1 / 9, abs=0.02)  # 1 km of a 3 km scene
    assert result["overlap_area_km2"] == pytest.approx(1.0, rel=0.02)
    assert result["first"]["kind"] == "map_sheet" and result["first"]["series"] == "osm"
    assert result["second"]["kind"] == "raster" and result["second"]["series"] is None
    assert result["first"]["georeferencing_basis"] == "file_metadata" == result["second"]["georeferencing_basis"]
    assert result["first"]["ground_pixel_m"] == pytest.approx(10.0, rel=0.01)
    assert result["second"]["ground_pixel_m"] == pytest.approx(50.0, rel=0.05)
    assert result["resolution_ratio"] == pytest.approx(5.0, rel=0.1)
    assert result["first"]["transformation_class"] == "exact" and result["second"]["transformation_class"] == "exact"
    assert any("do not show that features coincide" in c for c in result["cautions"])
    assert not any("datum shift" in c for c in result["cautions"])

    # Both are layers on the map, and only the sheet carries the series.
    listed = client.get(f"/api/v1/projects/{project['id']}/layers").json()["rasters"]
    assert {layer["title"]: layer["map_sheet"] is not None for layer in listed} == {"sheet.tif": True, "sat.tif": False}


def test_layers_that_do_not_overlap_say_so(client, project, headers, run_jobs):
    near = put(client, project, headers, run_jobs, "a.tif", media.geotiff_bytes(crs=UTM), series="osm")["asset"]
    far = put(
        client, project, headers, run_jobs, "b.tif",
        media.geotiff_bytes(crs=UTM, transform=from_origin(600000, 3100000, 10, 10)), series="osm",
    )["asset"]  # fmt: skip
    result = align(client, project, near, far).json()
    assert result["overlap"] is False and result["overlap_area_km2"] is None and result["overlap_bounds_4326"] is None
    assert "do not overlap" in result["findings"][0]


ABIDJAN = (-4.24, 7.69)  # Abidjan 1987 / UTM 30N: one registered shift, documented to 2 m (within the 10 m limit)


def test_approximate_datum_shifts_are_stated_and_added_up(client, project, headers, run_jobs):
    first = put(
        client, project, headers, run_jobs, "k.tif", geotiff_near("EPSG:2041", *ABIDJAN, size=(100, 100)), series="osm"
    )["asset"]
    other = put(
        client, project, headers, run_jobs, "k2.tif", geotiff_near("EPSG:2041", *ABIDJAN, size=(90, 90)), series="osm"
    )["asset"]
    result = align(client, project, first, other).json()
    assert result["first"]["transformation_class"] == "approximate" and result["first"]["datum_shift_accuracy_m"] == 2.0
    assert result["first"]["needs_acknowledgement"] is False
    text = " ".join(result["cautions"])
    assert "about 2 m" in text and "about 4 m" in text  # each layer's documented accuracy, and the two together


def test_a_layer_whose_datum_shift_needs_acknowledging_says_so_and_is_not_added_up(client, project, headers, run_jobs):
    # Kalianpur 1962: one registered shift, documented to 999 m, worse than the 10 m limit, never acknowledged.
    kalianpur = put(
        client, project, headers, run_jobs, "k.tif", geotiff_near("EPSG:24313", *DELHI, size=(100, 100)), series="osm"
    )["asset"]
    other = put(
        client, project, headers, run_jobs, "k2.tif", geotiff_near("EPSG:24313", *DELHI, size=(90, 90)), series="osm"
    )["asset"]
    result = align(client, project, kalianpur, other).json()
    assert result["first"]["needs_acknowledgement"] is True and result["second"]["needs_acknowledgement"] is True
    text = " ".join(result["cautions"])
    assert "has not been acknowledged" in text and "999" in text
    assert "1998" not in text  # nothing is added up for layers that are not carried to the map


def test_only_placed_rasters_in_the_project_can_be_compared(client, project, headers, run_jobs):
    placed = put(client, project, headers, run_jobs, "p.tif", media.geotiff_bytes(crs=UTM), series="osm")["asset"]
    unplaced = put(client, project, headers, run_jobs, "u.png", media.image_bytes("PNG"), series="esm")["asset"]
    refused = align(client, project, placed, unplaced)
    assert refused.status_code == 409 and refused.json()["code"] == "NOT_PLACED"
    assert align(client, project, placed, placed).status_code == 422
    photo = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        params={"modality": "ground_image", "filename": "p.jpg"},
        content=media.image_bytes("JPEG"),
        headers=headers,
    ).json()["asset"]
    assert align(client, project, placed, photo).status_code == 409
    missing = client.get(
        f"/api/v1/projects/{project['id']}/alignment",
        params={"first": placed["id"], "second": "00000000-0000-0000-0000-000000000000"},
    )
    assert missing.status_code == 404


# --- the pixels -------------------------------------------------------------------------------------------------------


def coverage(client, asset, z, x, y):
    response = client.get(f"/api/v1/assets/{asset['id']}/tiles/{z}/{x}/{y}.png")
    assert response.status_code == 200, response.text
    alpha = np.asarray(Image.open(io.BytesIO(response.content)))[:, :, 3] > 0
    columns, rows_ = np.where(alpha.any(axis=0))[0], np.where(alpha.any(axis=1))[0]
    return columns.min(), columns.max(), rows_.min(), rows_.max()


def test_the_same_ground_drawn_from_two_crs_lands_on_the_same_tile_pixels(client, project, headers, run_jobs):
    # A 400 m x 300 m rectangle, east of the 75 E central meridian so UTM grid north is almost true north.
    x0, y0, width_m, height_m = 500200.0, 3000000.0, 400.0, 300.0
    sheet = put(
        client, project, headers, run_jobs, "utm.tif",
        media.geotiff_bytes(crs=UTM, transform=from_origin(x0, y0, 5, 5), size=(80, 60)), series="osm",
    )["asset"]  # fmt: skip
    west, south, east, north = lonlat_box(x0, y0, width_m, height_m)
    satellite = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        params={"modality": "overhead_raster", "filename": "geo.tif"},
        content=media.geotiff_bytes(crs=WGS84, transform=from_bounds(west, south, east, north, 40, 30), size=(40, 30)),
        headers=headers,
    ).json()["asset"]
    run_jobs()

    z = 17
    centre_lon, centre_lat = (west + east) / 2, (south + north) / 2
    tx, ty, _, _ = tile_of(centre_lon, centre_lat, z)
    a = coverage(client, sheet, z, tx, ty)
    b = coverage(client, satellite, z, tx, ty)
    # (first column, last column, first row, last row) of the covered pixels in the same tile.
    assert a == pytest.approx(b, abs=2), (a, b)
    assert a[1] - a[0] > 20 and a[3] - a[2] > 15, "the rectangle should span many tile pixels"
