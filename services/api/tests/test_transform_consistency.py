"""Unified CRS transformation consistency (OSM/ESM Phase 3).

Every operation that moves coordinates between reference systems goes through the transformation planner. These tests
put the same ground point through every path that converts it (pixel lookup, position lookup, point conversion, layer
outline, measurement, area of interest, 3D terrain) and require the same longitude/latitude and the same provenance, for
exact, approximate, ambiguous, grid-missing and ballpark-only cases.

All rasters are synthetic. Kalianpur and Indian 1975 datums are used only because the local registry holds one or several
registered shifts for them. Nothing asserts or validates any Everest-56 or ESM definition, and none exists here.
Expected positions come from pyproj used directly, independently of the application's planner.
"""

from pathlib import Path

import pyproj
import pytest
from pyproj import Geod, Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.transformer import TransformerGroup
from rasterio.transform import from_origin

from geopoc.geo import convert, georeference, measure
from geopoc.geo import transform as T
from tests import media
from tests.test_assets import rows
from tests.test_crs_assessment import DELHI, THAILAND, geotiff_near, put, unreferenced
from tests.test_georeferencing import record
from tests.test_terrain import DECLARED, base, raster_bytes, terrain
from tests.test_terrain import put as put_dem

WGS84 = "EPSG:4326"
KALIANPUR_1880_ZONE_I = "EPSG:24371"
ONE_METRE = 1.0


def pixel(client, asset, **params):
    return client.get(f"/api/v1/assets/{asset['id']}/pixel", params=params)


def convert_point(client, source, target, x, y, **params):
    response = client.get(
        "/api/v1/crs/transform", params={"source": source, "target": target, "x": x, "y": y, **params}
    )
    return response


def measure_pixels(client, asset, **extra):
    body = {
        "kind": "distance",
        "space": "pixel",
        "asset_id": asset["id"],
        "points": [[10.5, 5.5], [20.5, 5.5]],
        **extra,
    }
    return client.post("/api/v1/measurements", json=body)


def metres_between(a, b) -> float:
    return Geod(ellps="WGS84").inv(a[0], a[1], b[0], b[1])[2]


def oracle(source_crs: str, x: float, y: float, lon: float, lat: float, *, ballpark_ok=False) -> tuple[float, float]:
    """Where pyproj itself puts (x, y): the best registered (non-ballpark) operation for the area, chosen without our code."""
    area = AreaOfInterest(lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1)
    group = TransformerGroup(source_crs, WGS84, always_xy=True, area_of_interest=area)
    usable = [
        t for t in group.transformers if "allpark" not in t.description and t.accuracy is not None and t.accuracy >= 0
    ]
    return usable[0].transform(x, y)


def layer_corner(client, project, asset) -> list[float]:
    layers = client.get(f"/api/v1/projects/{project['id']}/layers").json()
    layer = next(item for item in layers["rasters"] if item["asset_id"] == asset["id"])
    return layer["footprint_4326"]["coordinates"][0][0]  # the first outline point is the top-left corner


# --- exact and approximate: the same point, the same answer everywhere ------------------------------------------------

EXACT = ("EPSG:32643", None, "datum-unchanged", "exact", 0.0)
# An approximate shift within the 10 m limit, so it is used without acknowledgement (Abidjan 1987, 2 m). The 999 m
# Kalianpur shift is covered separately: it needs an acknowledgement and so cannot be a file-georeferenced layer.
ABIDJAN = (-4.24, 7.69)
APPROXIMATE = ("EPSG:2041", ABIDJAN, "EPSG:1470", "approximate", 2.0)


def raster_for(crs, near):
    if near is None:
        return media.geotiff_bytes(crs=crs, size=(40, 30))
    return geotiff_near(crs, *near, size=(40, 30))


@pytest.mark.parametrize(
    ("crs", "near", "operation_id", "klass", "accuracy"), [EXACT, APPROXIMATE], ids=["exact", "approximate"]
)
def test_one_point_gives_one_longitude_latitude_through_every_path(
    client, project, headers, run_jobs, crs, near, operation_id, klass, accuracy
):
    asset = put(client, project, headers, run_jobs, raster_for(crs, near))
    looked_up = pixel(client, asset, col=10, row=5).json()
    x, y = looked_up["native"]["x"], looked_up["native"]["y"]
    lon, lat = looked_up["lon_lat"]
    provenance = looked_up["transformation"]
    assert provenance["operation"]["id"] == operation_id and provenance["operation"]["classification"] == klass
    assert provenance["accuracy_m"] == accuracy and provenance["network"] == "disabled"
    assert looked_up["lon_lat_note"] is None

    # An independent oracle (pyproj, not our planner) agrees.
    expected = oracle(crs, x, y, lon, lat)
    assert (lon, lat) == pytest.approx(expected, abs=1e-12)

    # The point-conversion endpoint gives the same position and names the same operation.
    converted = convert_point(client, crs, WGS84, x, y).json()
    assert (converted["x"], converted["y"]) == pytest.approx((lon, lat), abs=1e-12)
    assert converted["transformation"]["operation_id"] == operation_id

    # A pixel measurement's points carry the same position and the same provenance.
    measured = measure_pixels(client, asset).json()
    assert measured["validity"] == "valid" and measured["points_lonlat_note"] is None
    assert measured["points_lonlat"][0] == pytest.approx([lon, lat], abs=1e-12)
    assert measured["transformation"]["operation"]["id"] == operation_id

    # Going the other way returns the pixel the point came from.
    back = pixel(client, asset, lon=lon, lat=lat).json()
    assert (back["col"], back["row"]) == (10, 5)

    # The map outline's top-left corner is the same conversion of the geotransform origin.
    state = client.get(f"/api/v1/assets/{asset['id']}/georeferencing").json()["current"]
    ox, oy = state["geotransform"][0], state["geotransform"][3]
    corner = layer_corner(client, project, asset)
    assert corner == pytest.approx(list(oracle(crs, ox, oy, lon, lat)), abs=1e-9)


def test_an_approximate_shift_really_moves_the_point_and_says_by_how_much(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, raster_for(*APPROXIMATE[:2]))
    looked_up = pixel(client, asset, col=10, row=5).json()
    x, y = looked_up["native"]["x"], looked_up["native"]["y"]
    # What a datum-blind conversion would give: only the projection undone, the Kalianpur 1962 longitude/latitude
    # read as if they were WGS 84.
    own = pyproj.CRS(APPROXIMATE[0])
    naive = Transformer.from_crs(own, own.geodetic_crs, always_xy=True).transform(x, y)
    shifted = looked_up["lon_lat"]
    assert 0 < metres_between(shifted, naive) <= 5000  # a real datum shift, not a relabelling
    assert looked_up["transformation"]["accuracy_m"] == 2.0


# --- ambiguous: nothing is chosen silently ------------------------------------------------------------------------------


@pytest.fixture
def ambiguous(client, project, headers, run_jobs):
    return put(client, project, headers, run_jobs, geotiff_near("EPSG:24047", *THAILAND, size=(40, 30)))


def test_an_ambiguous_raster_leaves_out_the_position_it_cannot_give(client, ambiguous):
    looked_up = pixel(client, ambiguous, col=10, row=5).json()
    assert looked_up["native"]["crs_id"] == "EPSG:24047"  # what the file says is still reported
    assert looked_up["values"] and looked_up["lon_lat"] is None and looked_up["transformation"] is None
    assert "Choose" in looked_up["lon_lat_note"] and "EPSG:1812" in looked_up["lon_lat_note"]

    by_position = pixel(client, ambiguous, lon=100.1, lat=14.1)
    assert by_position.status_code == 409
    problem = by_position.json()
    assert problem["code"] == "TRANSFORMATION_SELECTION_REQUIRED"
    assert {e["field"] for e in problem["errors"]} >= {"EPSG:1812", "EPSG:1304"}

    measured = measure_pixels(client, ambiguous).json()
    assert measured["validity"] == "valid" and measured["value"] > 0  # the measurement never needed WGS 84
    assert measured["points_lonlat"] is None and "Choose" in measured["points_lonlat_note"]
    assert "TRANSFORMATION_SELECTION_REQUIRED" in measured["flags"]


def test_a_chosen_operation_is_used_by_every_path_and_the_choices_differ(client, ambiguous):
    results = {}
    for choice in ("EPSG:1812", "EPSG:1304"):
        looked_up = pixel(client, ambiguous, col=10, row=5, operation=choice).json()
        x, y = looked_up["native"]["x"], looked_up["native"]["y"]
        lon_lat = looked_up["lon_lat"]
        assert looked_up["transformation"]["operation"]["id"] == choice
        converted = convert_point(client, "EPSG:24047", WGS84, x, y, operation=choice).json()
        assert (converted["x"], converted["y"]) == pytest.approx(lon_lat, abs=1e-12)
        measured = measure_pixels(client, ambiguous, transformation_operation=choice).json()
        assert measured["points_lonlat"][0] == pytest.approx(lon_lat, abs=1e-12)
        back = pixel(client, ambiguous, lon=lon_lat[0], lat=lon_lat[1], operation=choice).json()
        assert (back["col"], back["row"]) == (10, 5)
        results[choice] = lon_lat
    # The two registered shifts give different positions (that is why none is chosen for the person).
    assert metres_between(results["EPSG:1812"], results["EPSG:1304"]) > ONE_METRE


def test_an_operation_that_does_not_apply_is_refused_not_ignored(client, ambiguous):
    refused = pixel(client, ambiguous, lon=100.1, lat=14.1, operation="EPSG:99999")
    assert refused.status_code == 422 and refused.json()["code"] == "TRANSFORMATION_UNKNOWN_OPERATION"
    assert pixel(client, ambiguous, lon=100.1, lat=14.1, operation="ballpark").status_code == 422


def test_a_choice_recorded_with_the_georeferencing_is_the_one_every_path_uses(client, project, headers, run_jobs):
    asset = unreferenced(client, project, headers, run_jobs, *THAILAND)
    body = {"operation": "assign_crs", "crs_id": "EPSG:24047", "reason": "Stated in the delivery note."}
    recorded = record(client, headers, asset, **body, transformation_operation="EPSG:1812")
    assert recorded.status_code == 201, recorded.text

    plain = pixel(client, asset, col=10, row=5).json()
    assert plain["transformation"]["operation"]["id"] == "EPSG:1812"
    assert plain["transformation"]["operation"]["classification"] == "approximate"
    # A different request-level choice cannot override what was recorded (and reviewed) with the georeferencing.
    other = pixel(client, asset, col=10, row=5, operation="EPSG:1304").json()
    assert other["lon_lat"] == plain["lon_lat"]
    measured = measure_pixels(client, asset).json()
    assert measured["points_lonlat"][0] == pytest.approx(plain["lon_lat"], abs=1e-12)
    assert measured["transformation"]["operation"]["id"] == "EPSG:1812"
    assert pixel(client, asset, lon=plain["lon_lat"][0], lat=plain["lon_lat"][1]).json()["col"] == 10


# --- ballpark-only and missing grids ----------------------------------------------------------------------------------


def test_a_ballpark_only_crs_is_never_converted_silently(client, project, headers, run_jobs):
    blocked = put(client, project, headers, run_jobs, geotiff_near(KALIANPUR_1880_ZONE_I, *DELHI, size=(40, 30)))
    assert blocked["raster"]["georeferenced"] is False  # not placed at all

    looked_up = pixel(client, blocked, col=10, row=5).json()
    assert looked_up["lon_lat"] is None  # no apparently valid coordinate
    refused = convert_point(client, KALIANPUR_1880_ZONE_I, WGS84, 1000000, 1000000)
    assert refused.status_code == 409 and refused.json()["code"] == "TRANSFORMATION_BALLPARK_BLOCKED"
    assert [e["field"] for e in refused.json()["errors"]] == ["ballpark"]
    explicit = convert_point(client, KALIANPUR_1880_ZONE_I, WGS84, 1000000, 1000000, accept_ballpark=True).json()
    assert "BALLPARK_ACCEPTED" in explicit["warnings"] and explicit["operation"]["classification"] == "ballpark"


def test_a_registered_shift_that_needs_a_missing_grid_names_the_grid(client):
    refused = convert_point(client, "EPSG:4267", "EPSG:4269", -100.0, 40.0)  # NAD27 -> NAD83
    assert refused.status_code == 409 and refused.json()["code"] == "TRANSFORMATION_GRID_MISSING"
    assert "grid" in refused.json()["detail"].lower()


def test_measurement_survives_a_blocked_conversion_but_gives_no_longitude_latitude():
    """A pixel measurement is on the raster's own ellipsoid, so it stands even where WGS 84 cannot be reached."""
    crs = pyproj.CRS.from_authority("EPSG", KALIANPUR_1880_ZONE_I.split(":")[1])
    x, y = Transformer.from_crs(WGS84, crs, always_xy=True, allow_ballpark=True).transform(*DELHI)
    gt = [round(x / 10) * 10.0, 10.0, 0.0, round(y / 10) * 10.0, 0.0, -10.0]
    state = georeference.Effective(
        version=0, source="file", assertion_id=None, crs_id=KALIANPUR_1880_ZONE_I, crs_name=crs.name,
        crs_wkt=crs.to_wkt(), crs_usable=True, geotransform=gt, geotransform_usable=True, georeferenced=True,
        footprint=None, bounds_native=None, pixel_size=[10, 10], pixel_units="Indian yard",
    )  # fmt: skip
    result = measure.measure_pixels("distance", [[0, 0], [100, 0]], 200, 200, state, None)
    assert result.validity == "valid" and result.points_lonlat is None
    assert "TRANSFORMATION_BALLPARK_BLOCKED" in result.flags and "ballpark" in result.points_lonlat_note.lower()
    # 100 pixels of 10 Indian yards (0.9143985 m) on the plane; on the CRS's own ellipsoid it differs by the scale factor.
    assert result.planar["value"] == pytest.approx(100 * 10 * 0.9143985307, rel=1e-6)
    assert result.value == pytest.approx(result.planar["value"], rel=1e-2)  # the projection scale factor is not 1


# --- the converter itself ---------------------------------------------------------------------------------------------


def test_the_converter_classifies_and_refuses_as_the_planner_does():
    same = convert.convert(WGS84, WGS84, 75.0, 27.0)
    assert (
        (same.x, same.y) == (75.0, 27.0) and same.provenance["operation"]["id"] == "identity" and same.resolved is None
    )

    exact = convert.to_geographic("EPSG:32643", [500000.0, 500010.0], [3000000.0, 3000010.0])
    assert exact.classification == "exact" and exact.provenance["accuracy_m"] == 0.0
    assert list(exact.x) == pytest.approx([75.0, 75.0001], abs=1e-4)

    kalianpur = Transformer.from_crs(WGS84, "EPSG:24313", always_xy=True).transform(*DELHI)
    with pytest.raises(T.TransformationError) as needs_ack:  # 999 m: worse than the 10 m limit
        convert.to_geographic("EPSG:24313", *kalianpur)
    assert needs_ack.value.code == "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED"
    assert needs_ack.value.extra["accuracy_m"] == 999.0 and needs_ack.value.extra["threshold_m"] == 10.0
    stored = T.Acknowledgement(
        needs_ack.value.extra["operation_id"],
        needs_ack.value.extra["source_crs"],
        needs_ack.value.extra["target_crs"],
        needs_ack.value.extra["accuracy_m"],
    )
    approximate = convert.to_geographic("EPSG:24313", *kalianpur, acknowledgement=stored)
    assert approximate.provenance["operation"]["id"] == "EPSG:1247" and approximate.provenance["accuracy_m"] == 999.0
    assert approximate.provenance["operation"]["acknowledgement"]["operation_id"] == "EPSG:1247"
    within_limit = convert.to_geographic("EPSG:2041", *Transformer.from_crs(WGS84, "EPSG:2041").transform(7.69, -4.24))
    assert within_limit.provenance["operation"]["requires_acknowledgement"] is False

    with pytest.raises(T.TransformationError) as ambiguous:
        convert.from_geographic("EPSG:24047", *THAILAND)
    assert ambiguous.value.code == "TRANSFORMATION_SELECTION_REQUIRED" and ambiguous.value.plan.usable
    chosen = convert.from_geographic("EPSG:24047", *THAILAND, operation_id="EPSG:1812")
    assert chosen.provenance["operation"]["chosen_by"] == "selection"

    with pytest.raises(T.TransformationError) as ballpark:
        convert.convert(KALIANPUR_1880_ZONE_I, WGS84, 1000000.0, 1000000.0)
    assert ballpark.value.code == "TRANSFORMATION_BALLPARK_BLOCKED"
    accepted = convert.convert(KALIANPUR_1880_ZONE_I, WGS84, 1000000.0, 1000000.0, accept_ballpark=True)
    assert accepted.provenance["operation"]["ballpark_accepted"] is True

    with pytest.raises(T.TransformationError) as grid:
        convert.convert("EPSG:4267", "EPSG:4269", -100.0, 40.0)
    assert grid.value.code == "TRANSFORMATION_GRID_MISSING"

    with pytest.raises(T.TransformationError) as outside:
        convert.to_geographic("EPSG:32643", 1e12, 1e12)
    assert outside.value.code == "TRANSFORM_FAILED"


# --- area of interest -------------------------------------------------------------------------------------------------


def aoi_body(crs, near, name="With AOI", **extra):
    x, y = Transformer.from_crs(WGS84, crs, always_xy=True, allow_ballpark=True).transform(*near)
    return {"name": name, "aoi": {"geometry": {"type": "Point", "coordinates": [x, y]}, "crs_id": crs, **extra}}


def create(client, headers, body):
    return client.post("/api/v1/projects", json=body, headers=headers)


def project_count() -> int:
    return rows("SELECT count(*) FROM project")[0][0]


def test_an_area_of_interest_is_moved_with_the_same_policy_and_keeps_the_provenance(client, headers):
    wgs = create(client, headers, aoi_body(WGS84, (77.2, 28.6))).json()["aoi"]
    assert wgs["source"]["transformation"]["operation"]["id"] == "identity"
    assert wgs["geometry_4326"]["coordinates"] == pytest.approx([77.2, 28.6])

    utm = create(client, headers, aoi_body("EPSG:32643", (75.0, 27.1), "UTM")).json()["aoi"]
    assert utm["source"]["transformation"]["operation"]["id"] == "datum-unchanged"
    assert utm["geometry_4326"]["coordinates"] == pytest.approx([75.0, 27.1], abs=1e-9)

    refused = create(client, headers, aoi_body("EPSG:24313", DELHI, "Kalianpur"))
    assert refused.status_code == 409 and refused.json()["code"] == "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED"
    assert refused.json()["context"]["accuracy_m"] == 999.0 and refused.json()["context"]["threshold_m"] == 10.0
    shifted = create(client, headers, aoi_body("EPSG:24313", DELHI, "Kalianpur", acknowledge_accuracy=True)).json()[
        "aoi"
    ]
    transformation = shifted["source"]["transformation"]
    assert transformation["operation"]["acknowledgement"]["operation_id"] == "EPSG:1247"
    assert transformation["operation"]["acknowledgement"]["acknowledged_by"]  # who, recorded with the AOI
    assert transformation["operation"]["id"] == "EPSG:1247" and transformation["accuracy_m"] == 999.0
    assert transformation["operation"]["classification"] == "approximate"
    x, y = shifted["source"]["geometry"]["coordinates"]
    assert shifted["geometry_4326"]["coordinates"] == pytest.approx(list(oracle("EPSG:24313", x, y, *DELHI)), abs=1e-9)


def test_an_area_of_interest_that_needs_a_choice_is_refused_until_one_is_made(client, headers):
    before = project_count()
    body = aoi_body("EPSG:24047", THAILAND, "Ambiguous")
    refused = create(client, headers, body)
    assert refused.status_code == 409 and refused.json()["code"] == "TRANSFORMATION_SELECTION_REQUIRED"
    assert {e["field"] for e in refused.json()["errors"]} >= {"EPSG:1812", "EPSG:1304"}
    assert project_count() == before  # nothing was stored

    body["aoi"]["transformation_operation"] = "EPSG:1812"
    created = create(client, headers, body)
    assert created.status_code == 201, created.text
    transformation = created.json()["aoi"]["source"]["transformation"]
    assert transformation["operation"]["id"] == "EPSG:1812" and transformation["operation"]["chosen_by"] == "selection"

    # Changing the area of interest later follows the same rule.
    other = create(client, headers, {"name": "Plain"}).json()
    again = client.patch(
        f"/api/v1/projects/{other['id']}", json={"aoi": aoi_body("EPSG:24047", THAILAND)["aoi"]}, headers=headers
    )
    assert again.status_code == 409 and again.json()["code"] == "TRANSFORMATION_SELECTION_REQUIRED"
    assert client.get(f"/api/v1/projects/{other['id']}").json()["aoi"] is None


def test_an_area_of_interest_in_a_ballpark_only_crs_is_refused(client, headers):
    before = project_count()
    refused = create(client, headers, aoi_body(KALIANPUR_1880_ZONE_I, DELHI, "Ballpark"))
    assert refused.status_code == 409 and refused.json()["code"] == "TRANSFORMATION_BALLPARK_BLOCKED"
    assert project_count() == before


def test_an_area_of_interest_outside_the_crs_domain_keeps_its_old_error(client, headers):
    body = {"name": "Bad", "aoi": {"geometry": {"type": "Point", "coordinates": [1e12, 1e12]}, "crs_id": "EPSG:32643"}}
    refused = create(client, headers, body)
    assert refused.status_code == 422 and refused.json()["code"] == "AOI_COORDINATES_OUT_OF_RANGE"


# --- 3D terrain: horizontal position only, elevation untouched -----------------------------------------------------------


def dem_in(crs, near, **kwargs):
    x, y = Transformer.from_crs(WGS84, crs, always_xy=True).transform(*near)
    step = 0.001 if crs == "EPSG:4240" else 10
    return raster_bytes(
        crs=crs, transform=from_origin(round(x / step) * step, round(y / step) * step, step, step), **kwargs
    )


def test_terrain_is_positioned_by_the_same_planner_and_its_values_never_change(client, project, headers, run_jobs):
    exact = put_dem(client, project, headers, run_jobs, raster_bytes(**DECLARED), "exact.tif")
    meta = terrain(client, project, exact).json()
    assert meta["positioning"]["state"] == "positioned"
    assert meta["positioning"]["transformation"]["operation"]["id"] == "datum-unchanged"
    ex = meta["extent_native"]
    corners = [
        (ex["x_min"], ex["y_min"]),
        (ex["x_max"], ex["y_min"]),
        (ex["x_min"], ex["y_max"]),
        (ex["x_max"], ex["y_max"]),
    ]
    lons, lats = Transformer.from_crs("EPSG:32643", WGS84, always_xy=True).transform(
        [c[0] for c in corners], [c[1] for c in corners]
    )
    assert meta["bbox_4326"] == pytest.approx([min(lons), min(lats), max(lons), max(lats)], abs=1e-9)

    dem = put_dem(client, project, headers, run_jobs, dem_in("EPSG:24047", THAILAND, **DECLARED), "ambiguous.tif")
    unplaced = terrain(client, project, dem).json()
    assert unplaced["bbox_4326"] is None and unplaced["centre_4326"] is None
    assert unplaced["positioning"]["state"] == "not_positioned"
    assert unplaced["positioning"]["code"] == "TRANSFORMATION_SELECTION_REQUIRED"
    assert set(unplaced["positioning"]["candidates"]) == {"EPSG:1812", "EPSG:1304"}
    # The surface itself is drawn all the same: same grid, same elevations, same vertical datum statement.
    chosen = terrain(client, project, dem, operation="EPSG:1812").json()
    assert chosen["positioning"]["state"] == "positioned" and chosen["bbox_4326"] is not None
    assert chosen["positioning"]["transformation"]["operation"]["id"] == "EPSG:1812"
    assert chosen["elevation"] == unplaced["elevation"] and chosen["vertical_datum"] == unplaced["vertical_datum"]
    assert chosen["cell_size_m"] == unplaced["cell_size_m"] and chosen["extent_native"] == unplaced["extent_native"]
    ex = chosen["extent_native"]
    centre = convert_point(
        client,
        "EPSG:24047",
        WGS84,
        (ex["x_min"] + ex["x_max"]) / 2,
        (ex["y_min"] + ex["y_max"]) / 2,
        operation="EPSG:1812",
    )
    assert chosen["centre_4326"] == pytest.approx([centre.json()["x"], centre.json()["y"]], abs=1e-9)

    a = client.get(f"{base(project)}/assets/{dem['id']}/terrain/grid").content
    b = client.get(f"{base(project)}/assets/{dem['id']}/terrain/grid", params={"operation": "EPSG:1812"}).content
    assert a == b  # elevation bytes do not depend on any horizontal transformation


def test_a_geographic_raster_on_another_datum_is_no_longer_assumed_to_be_wgs84(client, project, headers, run_jobs):
    """Before: the native degrees were used as WGS 84 longitude/latitude without a word."""
    wgs = put_dem(
        client,
        project,
        headers,
        run_jobs,
        raster_bytes(crs=WGS84, transform=from_origin(75, 27, 0.001, 0.001), **DECLARED),
        "wgs.tif",
    )
    positioned = terrain(client, project, wgs).json()
    assert positioned["positioning"]["transformation"]["operation"]["id"] == "identity"
    assert positioned["bbox_4326"][0] == pytest.approx(75.0)

    indian = put_dem(client, project, headers, run_jobs, dem_in("EPSG:4240", THAILAND, **DECLARED), "indian.tif")
    meta = terrain(client, project, indian).json()
    assert meta["horizontal_unit"] == "degree" and meta["bbox_4326"] is None
    assert meta["positioning"]["code"] == "TRANSFORMATION_SELECTION_REQUIRED"


# --- guard: no new default-PROJ conversion can appear unreviewed -------------------------------------------------------------

# Every file allowed to build a pyproj Transformer directly, and why. A new one fails this test until it is either routed
# through geopoc.geo.convert or added here with a reason.
DIRECT_TRANSFORMER_FILES = {
    "geo/transform.py": "the planner itself, and locate() which only decides which regional operations apply",
    "geo/convert.py": "uses the planner's operations; no direct Transformer",
    "geo/measure.py": "a CRS to its own geographic CRS: same datum, no datum change; geodesic on its own ellipsoid",
    "geo/georeference.py": "WGS 84 to Web Mercator for tile indexing: same datum, no datum change",
    "ingestion/raster.py": "locator only: decides which regional operations apply to a raster, never a result",
    "api/routers/layers.py": "locator only: area-of-use warning on a point conversion that already used the planner",
    "geo/orientation.py": "a CRS to its own geographic CRS: same datum, no datum change; measures local ground directions",
    "geo/projenv.py": "fixed datum-free pairs, only to compare pyproj's PROJ with GDAL's PROJ; never a result",
}


def test_no_unreviewed_direct_proj_conversion_exists_in_the_application():
    package = Path(__file__).resolve().parents[1] / "geopoc"
    found = set()
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "Transformer.from_crs(" in text or "Transformer.from_pipeline(" in text:
            found.add(path.relative_to(package).as_posix())
    unreviewed = found - set(DIRECT_TRANSFORMER_FILES) - {"geo/convert.py"}
    assert not unreviewed, f"direct PROJ conversions not routed through geopoc.geo.convert: {sorted(unreviewed)}"
    stale = set(DIRECT_TRANSFORMER_FILES) - found - {"geo/convert.py"}
    assert not stale, f"allow-list names files that no longer build a Transformer: {sorted(stale)}"
