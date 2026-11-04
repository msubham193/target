"""Rasters whose footprint crosses the antimeridian (CRS correctness hardening, Phase 2).

Only the footprint is supported: it is split at 180° into a MultiPolygon and flagged. Everything that needs one
continuous area or distance on the ground refuses a crossing raster with ANTIMERIDIAN_CROSSING_UNSUPPORTED instead of
reporting a CRS-domain error or dropping geometry. Rasters are synthetic.
"""

import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import MultiPolygon, Polygon

from geopoc.geo import antimeridian as A
from tests import media
from tests.test_crs_assessment import put
from tests.test_transform_consistency import measure_pixels, pixel

CODE = "ANTIMERIDIAN_CROSSING_UNSUPPORTED"


# --- detection ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lons", "expected"),
    [
        ([10.0, 11.0, 11.0, 10.0], False),  # ordinary
        ([-120.0, -119.0], False),
        ([179.6, 179.9, -179.9, -179.6], True),  # straddles 180
        ([-179.6, -179.9, 179.9, 179.6], True),  # the same, the other way round
        ([170.0, 180.0, 180.0, 170.0], False),  # only touches 180 with its east edge
        ([-180.0, -170.0, -170.0, -180.0], False),  # only touches -180 with its west edge
        ([179.0, -180.0, -180.0, 179.0], False),  # -180 is the same meridian as 180: a touch, not a crossing
        ([0.0], False),
    ],
)
def test_crossing_is_detected_and_touching_the_meridian_is_not_a_crossing(lons, expected):
    assert A.crosses(lons) is expected


def test_an_edge_exactly_on_the_meridian_is_a_touch_whichever_sign_it_is_reported_with():
    assert A.crosses([170.0, 180.0, 180.0, 170.0]) is False
    assert A.crosses([170.0, -180.0, -180.0, 170.0]) is False  # -180 is the same meridian: snapped to the east side
    assert list(A.unwrap([170.0, -180.0])) == [170.0, 180.0]


def test_values_just_past_the_range_are_folded_but_other_out_of_range_values_are_not_wrapped():
    assert list(A.normalise([179.5, 180.5])) == pytest.approx([179.5, -179.5])
    assert A.normalise([180.0 + 1e-12, 179.0]) is not None  # rounding noise
    assert A.normalise([200.0, 210.0]) is None  # a 0-360 grid or a projected coordinate: an error, not a position
    assert A.normalise([float("nan"), 1.0]) is None


def test_unwrap_makes_the_outline_continuous():
    assert list(A.unwrap([179.6, 179.9, -179.9, -179.6])) == pytest.approx([179.6, 179.9, 180.1, 180.4])
    assert list(A.unwrap([10.0, 11.0])) == [10.0, 11.0]  # not a crossing: unchanged


# --- splitting ------------------------------------------------------------------------------------------------------------


def test_a_crossing_polygon_is_split_into_two_valid_parts_that_keep_all_the_area():
    outline = Polygon([(179.5, 10), (180.5, 10), (180.5, 11), (179.5, 11)])
    split = A.split_polygon(outline)
    assert isinstance(split, MultiPolygon) and split.is_valid and len(split.geoms) == 2
    assert split.area == pytest.approx(outline.area)
    west, east = sorted(split.geoms, key=lambda part: part.bounds[0])
    assert east.bounds == pytest.approx((-180.0, 10, -179.5, 11)) or west.bounds == pytest.approx(
        (-180.0, 10, -179.5, 11)
    )
    assert all(-180.0 <= x <= 180.0 for part in split.geoms for x, _y in part.exterior.coords)
    assert max(part.bounds[2] for part in split.geoms) == pytest.approx(180.0)


def test_a_polygon_wholly_on_one_side_is_only_shifted_into_range():
    beyond = Polygon([(180.2, 0), (180.8, 0), (180.8, 1), (180.2, 1)])
    shifted = A.split_polygon(beyond)
    assert shifted.bounds == pytest.approx((-179.8, 0, -179.2, 1))


def test_outline_builds_the_footprint_and_says_whether_it_crosses():
    ordinary = A.outline([10, 11, 11, 10], [0, 0, 1, 1])
    assert ordinary is not None and isinstance(ordinary[0], Polygon) and ordinary[1] is False
    geometry, crossing = A.outline([179.5, -179.5, -179.5, 179.5], [10, 10, 11, 11])
    assert crossing is True and isinstance(geometry, MultiPolygon) and geometry.is_valid
    assert geometry.area == pytest.approx(1.0)
    touching, crossing = A.outline([179, 180, 180, 179], [10, 10, 11, 11])
    assert isinstance(touching, Polygon) and crossing is False and touching.bounds[2] == 180.0


@pytest.mark.parametrize(
    ("lons", "lats"),
    [([179, 181, 181, 179], [0, 0, 100, 100]), ([0, 1, 1, 0], [0, 0, 0, 0]), ([0, 1, 1, np.nan], [0, 0, 1, 1])],
)
def test_outlines_that_are_not_footprints_are_refused(lons, lats):
    assert A.outline(lons, lats) is None  # latitude past 90, zero area, not finite


def test_a_stored_split_footprint_is_recognised_from_the_geometry_alone():
    geometry, _ = A.outline([179.5, -179.5, -179.5, 179.5], [10, 10, 11, 11])
    assert A.geometry_crosses(geometry) is True
    assert A.geometry_crosses(Polygon([(0, 0), (1, 0), (1, 1)])) is False
    two_apart = MultiPolygon([Polygon([(0, 0), (1, 0), (1, 1)]), Polygon([(5, 5), (6, 5), (6, 6)])])
    assert A.geometry_crosses(two_apart) is False and A.geometry_crosses(None) is False
    assert A.record_fields(True) == {
        "crosses_antimeridian": True,
        "footprint_representation": "split_multipolygon",
        "split_boundary_degrees": 180,
    }
    assert A.record_fields(False) == {"crosses_antimeridian": False}


# --- through the application -----------------------------------------------------------------------------------------------


def utm60_across_180(client, project, headers, run_jobs):
    """100 x 100 km in UTM 60N whose east side is past longitude 180° (zone 60 ends at 180° at the equator)."""
    data = media.geotiff_bytes(crs="EPSG:32660", transform=from_origin(700000, 5050000, 1000, 1000), size=(100, 100))
    return put(client, project, headers, run_jobs, data)


def geographic(client, project, headers, run_jobs, west, north=10.0, size=(100, 100), pixel_size=0.01):
    data = media.geotiff_bytes(crs="EPSG:4326", transform=from_origin(west, north, pixel_size, pixel_size), size=size)
    return put(client, project, headers, run_jobs, data)


def footprint_of(client, project, asset):
    layers = client.get(f"/api/v1/projects/{project['id']}/layers").json()
    return next((r for r in layers["rasters"] if r["asset_id"] == asset["id"]), None)


def test_a_crossing_raster_is_georeferenced_with_a_split_footprint_and_says_so(client, project, headers, run_jobs):
    asset = utm60_across_180(client, project, headers, run_jobs)
    assert asset["metadata"]["footprint_transformation"]["crosses_antimeridian"] is True
    record = asset["metadata"]["footprint_transformation"]
    assert record["footprint_representation"] == "split_multipolygon" and record["split_boundary_degrees"] == 180
    assert "CROSSES_ANTIMERIDIAN" in asset["quality_flags"] and "antimeridian" in asset["unknowns"]
    assert "INVALID_GEOREFERENCING" not in asset["quality_flags"]  # no longer misreported as a bad CRS coordinate
    assert "outside the valid" not in " ".join(asset["unknowns"].values()).lower()


def test_a_crossing_raster_is_not_drawn_and_the_layer_list_says_why(client, project, headers, run_jobs):
    asset = utm60_across_180(client, project, headers, run_jobs)
    layers = client.get(f"/api/v1/projects/{project['id']}/layers").json()
    assert footprint_of(client, project, asset) is None
    [excluded] = [e for e in layers["excluded"] if e["asset_id"] == asset["id"]]
    assert excluded["reason"] == CODE and "180" in excluded["detail"]


def test_unsupported_consumers_refuse_a_crossing_raster_clearly(client, project, headers, run_jobs):
    asset = utm60_across_180(client, project, headers, run_jobs)
    tile = client.get(f"/api/v1/assets/{asset['id']}/tiles/3/7/2.png")
    assert tile.status_code == 422 and tile.json()["code"] == CODE
    by_position = pixel(client, asset, lon=-179.0, lat=45.0)
    assert by_position.status_code == 422 and by_position.json()["code"] == CODE
    measured = measure_pixels(client, asset)
    assert measured.status_code == 422 and measured.json()["code"] == CODE
    by_pixel = pixel(client, asset, col=10, row=5).json()
    assert by_pixel["lon_lat"] is None and "180" in by_pixel["lon_lat_note"]
    for response in (tile, by_position, measured):
        assert "CRS" not in response.json()["title"] and "outside the valid" not in response.json()["detail"]


def test_a_geographic_raster_across_180_is_a_crossing_not_an_invalid_coordinate(client, project, headers, run_jobs):
    asset = geographic(client, project, headers, run_jobs, west=179.5)  # 179.5 to 180.5
    assert asset["metadata"]["footprint_transformation"]["crosses_antimeridian"] is True
    assert "CROSSES_ANTIMERIDIAN" in asset["quality_flags"]


@pytest.mark.parametrize("west", [179.0, -180.0], ids=["east edge on 180", "west edge on -180"])
def test_a_raster_that_only_touches_the_meridian_is_an_ordinary_footprint(client, project, headers, run_jobs, west):
    asset = geographic(client, project, headers, run_jobs, west=west, size=(100, 100), pixel_size=0.01)
    if west == 179.0:  # 179.0 to 180.0 exactly
        assert asset["metadata"]["footprint_transformation"]["crosses_antimeridian"] is False
    layer = footprint_of(client, project, asset)
    assert layer is not None and layer["footprint_4326"]["type"] == "Polygon"
    assert "CROSSES_ANTIMERIDIAN" not in asset["quality_flags"]
    assert client.get(f"/api/v1/assets/{asset['id']}/tiles/9/511/255.png").status_code in (200, 204)


def test_an_ordinary_raster_is_unaffected(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, media.geotiff_bytes(crs="EPSG:32643", size=(40, 30)))
    assert asset["metadata"]["footprint_transformation"]["crosses_antimeridian"] is False
    assert footprint_of(client, project, asset)["footprint_4326"]["type"] == "Polygon"
