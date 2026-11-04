"""Transformation planning: exact, approximate, ballpark, ambiguous and unavailable operations.

These tests use only the local PROJ database and rasterio (no application database). They assert what the registry
says about registered CRSs (for example the documented accuracy of a named datum shift), so they are tied to the
pinned PROJ version; a registry change that alters one of these figures should be looked at, not silenced.

Nothing here claims anything about Everest-56 or any map series: no such definition is installed or assumed. The
Kalianpur and Indian datums are used only because the registry holds several registered shifts for them.
"""

import io

import numpy as np
import pyproj
import pytest
import rasterio
from PIL import Image
from pyproj import CRS, Transformer
from rasterio.transform import from_origin

from geopoc.geo import tiles
from geopoc.geo import transform as T

WGS84 = "EPSG:4326"
INDIA = (77.0, 27.0, 77.5, 27.5)  # a box inside the area of use of the Kalianpur shifts that cover India
THAILAND = (100.0, 14.0, 100.5, 14.5)


def test_proj_network_access_is_off():
    assert pyproj.network.is_network_enabled() is False


# --- exact: no datum change --------------------------------------------------------------------------------------


def test_utm_to_wgs84_is_exact_and_matches_pyproj():
    plan = T.plan("EPSG:32643", WGS84)
    assert plan.status == "exact"
    [operation] = plan.operations
    assert (operation.klass, operation.accuracy_m, operation.id) == ("exact", 0.0, T.EXACT_ID)
    lon, lat = operation.transformer.transform(500000, 3000000)
    reference = Transformer.from_crs("EPSG:32643", WGS84, always_xy=True).transform(500000, 3000000)
    assert (lon, lat) == pytest.approx(reference, abs=1e-12)
    assert lon == pytest.approx(75.0, abs=1e-9)  # 500000 m east is the central meridian of zone 43


def test_wgs84_round_trip_through_utm_is_exact():
    forward = T.resolve(WGS84, "EPSG:32643").operation.transformer
    backward = T.resolve("EPSG:32643", WGS84).operation.transformer
    easting, northing = forward.transform(77.2, 28.6)
    lon, lat = backward.transform(easting, northing)
    assert (lon, lat) == pytest.approx((77.2, 28.6), abs=1e-9)


def test_wgs84_to_wgs84_and_web_mercator_are_exact():
    assert T.plan(WGS84, WGS84).status == "exact"
    assert T.plan("EPSG:32643", "EPSG:3857").status == "exact"
    assert T.resolve("EPSG:32643", "EPSG:3857").pipeline is None  # nothing to override in GDAL


def test_an_exact_plan_is_not_a_choice():
    resolved = T.resolve("EPSG:32643", WGS84)
    assert not resolved.accepted_ballpark
    assert resolved.plan.messages == []


# --- ballpark: blocked by default ------------------------------------------------------------------------------------

CUSTOM_POLYCONIC = CRS.from_proj4(
    # Placeholder parameters for the test only. They are not an Everest-56 or ESM definition.
    "+proj=poly +lat_0=24 +lon_0=80 +a=6377301.243 +rf=300.8017255 +units=m +no_defs"
)


def test_a_crs_with_no_datum_shift_information_is_ballpark_only():
    plan = T.plan(CUSTOM_POLYCONIC, WGS84)
    assert plan.status == "unavailable"
    [operation] = plan.operations
    assert operation.klass == "ballpark"
    assert operation.accuracy_m is None
    assert operation.usable is False


def test_ballpark_is_refused_unless_explicitly_accepted():
    with pytest.raises(T.TransformationError) as refused:
        T.resolve(CUSTOM_POLYCONIC, WGS84)
    assert (refused.value.status, refused.value.code) == (409, "TRANSFORMATION_BALLPARK_BLOCKED")
    assert "blocked" in refused.value.detail.lower()

    accepted = T.resolve(CUSTOM_POLYCONIC, WGS84, accept_ballpark=True)
    assert accepted.accepted_ballpark is True
    assert accepted.operation.klass == "ballpark"


def test_a_registered_crs_whose_datum_has_no_registered_shift_is_ballpark_only():
    # Kalianpur 1880 / India zone I: the registry has the projection, but no registered shift to WGS 84.
    plan = T.plan("EPSG:24371", WGS84, INDIA)
    assert plan.status == "unavailable"
    assert {o.klass for o in plan.operations} == {"ballpark"}
    with pytest.raises(T.TransformationError) as refused:
        T.resolve("EPSG:24371", WGS84, INDIA)
    assert refused.value.code == "TRANSFORMATION_BALLPARK_BLOCKED"


def test_naming_the_ballpark_operation_does_not_bypass_the_block():
    with pytest.raises(T.TransformationError) as refused:
        T.resolve("EPSG:24371", WGS84, INDIA, operation_id=T.BALLPARK_ID)
    assert refused.value.code == "TRANSFORMATION_BALLPARK_BLOCKED"


# --- approximate: documented accuracy -------------------------------------------------------------------------------


def acknowledgement_for(source, target, operation_id, bounds=None):
    """An acknowledgement of exactly the operation the planner offers, made the way the application makes one."""
    operation = T.plan(source, target, bounds).find(operation_id)
    return T.Acknowledgement(
        operation.id, T.crs_label(pyproj.CRS(source)), T.datum_label(pyproj.CRS(target)), operation.accuracy_m
    )


def test_one_registered_shift_in_the_area_is_approximate_with_its_documented_accuracy():
    plan = T.plan("EPSG:4145", WGS84, INDIA)  # Kalianpur 1962
    assert plan.status == "approximate"
    [operation] = plan.usable
    assert operation.klass == "approximate"
    assert operation.id == "EPSG:1247"
    assert operation.accuracy_m == 999.0  # the registry's own figure for this shift, not an estimate of ours
    assert "999" in operation.summary()
    # Only one applies, so no choice is needed, but 999 m is worse than the 10 m limit: it needs an acknowledgement.
    with pytest.raises(T.TransformationError) as refused:
        T.resolve("EPSG:4145", WGS84, INDIA)
    assert refused.value.code == "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED"
    resolved = T.resolve(
        "EPSG:4145", WGS84, INDIA, acknowledgement=acknowledgement_for("EPSG:4145", WGS84, "EPSG:1247", INDIA)
    )
    assert resolved.operation.id == "EPSG:1247"


def test_the_ellipsoid_is_not_enough_to_make_a_shift_exact():
    # Same ellipsoid family, different datums: the shift is approximate, never "exact".
    for source in ("EPSG:4145", "EPSG:4144", "EPSG:4146"):
        plan = T.plan(source, WGS84, INDIA)
        assert plan.status != "exact", source


# --- ambiguous: a person must choose --------------------------------------------------------------------------------


def test_several_registered_shifts_require_an_explicit_choice():
    plan = T.plan("EPSG:4145", WGS84)  # no area given: every registered shift for the pair
    assert plan.status == "selection_required"
    approximate = [o for o in plan.operations if o.klass == "approximate"]
    assert len(approximate) >= 2
    assert {o.id for o in approximate} >= {"EPSG:1247", "EPSG:15701"}
    assert plan.recommended_id == "EPSG:15701"  # smallest documented accuracy: a hint, not a choice

    with pytest.raises(T.TransformationError) as refused:
        T.resolve("EPSG:4145", WGS84)
    assert (refused.value.status, refused.value.code) == (409, "TRANSFORMATION_SELECTION_REQUIRED")
    assert "EPSG:1247" in refused.value.detail and "EPSG:15701" in refused.value.detail


def test_the_recommendation_is_never_applied_on_its_own():
    with pytest.raises(T.TransformationError):
        T.resolve("EPSG:4145", WGS84)
    assert T.plan("EPSG:4145", WGS84).recommended_id is not None


def test_the_chosen_operation_is_the_one_used_and_the_choice_matters():
    # EPSG:1247 is 999 m: choosing it is not enough, it is also acknowledged (EPSG:15701 is within 10 m and is not).
    coarse = T.resolve(
        "EPSG:4145",
        WGS84,
        operation_id="EPSG:1247",
        acknowledgement=acknowledgement_for("EPSG:4145", WGS84, "EPSG:1247"),
    ).operation.transformer.transform(77.2, 27.1)
    fine = T.resolve("EPSG:4145", WGS84, operation_id="EPSG:15701").operation.transformer.transform(77.2, 27.1)
    assert coarse != fine, "the two registered shifts give different positions; that is why a choice is required"
    metres = pyproj.Geod(ellps="WGS84").inv(*coarse, *fine)[2]
    assert metres > 1, "the shifts differ by metres or more at this point"


def test_an_operation_that_does_not_apply_is_rejected_with_the_valid_choices():
    with pytest.raises(T.TransformationError) as refused:
        T.resolve("EPSG:4145", WGS84, operation_id="EPSG:9999")
    assert (refused.value.status, refused.value.code) == (422, "TRANSFORMATION_UNKNOWN_OPERATION")
    assert "EPSG:1247" in refused.value.detail


def test_a_choice_made_for_one_target_names_the_same_shift_for_another():
    to_geographic = T.plan("EPSG:4145", WGS84, INDIA)
    to_mercator = T.plan("EPSG:4145", tiles.WEB_MERCATOR, INDIA)
    assert [o.id for o in to_geographic.usable] == [o.id for o in to_mercator.usable] == ["EPSG:1247"]


def test_indian_1975_near_thailand_has_two_registered_shifts():
    plan = T.plan("EPSG:24047", WGS84, THAILAND)
    assert plan.status == "selection_required"
    assert {o.id for o in plan.usable} == {"EPSG:1812", "EPSG:1304"}
    assert {o.accuracy_m for o in plan.usable} == {3.0, 5.0}


# --- missing grids: listed, never used --------------------------------------------------------------------------------


def test_operations_that_need_an_uninstalled_grid_are_listed_and_never_used():
    plan = T.plan("EPSG:4277", "EPSG:4258")  # OSGB36 to ETRS89: the accurate shift needs the OSTN15 grid
    assert plan.status == "unavailable"
    missing = [o for o in plan.operations if o.klass == "unavailable"]
    assert [g.name for o in missing for g in o.grids] == ["uk_os_OSTN15_NTv2_OSGBtoETRS.tif"]
    assert missing[0].accuracy_m == pytest.approx(0.03)
    assert plan.missing_grids() == ["uk_os_OSTN15_NTv2_OSGBtoETRS.tif"]

    with pytest.raises(T.TransformationError) as refused:
        T.resolve("EPSG:4277", "EPSG:4258")
    assert (refused.value.status, refused.value.code) == (409, "TRANSFORMATION_GRID_MISSING")
    assert "uk_os_OSTN15_NTv2_OSGBtoETRS.tif" in refused.value.detail
    assert "Nothing was downloaded" in refused.value.detail


def test_a_missing_grid_is_reported_even_when_a_ballpark_fallback_exists():
    with pytest.raises(T.TransformationError) as refused:
        T.resolve("EPSG:4267", "EPSG:4269")  # NAD27 to NAD83: many registered shifts, all grid-based, plus a ballpark
    assert refused.value.code == "TRANSFORMATION_GRID_MISSING"
    assert "us_noaa_conus.tif" in refused.value.detail


def test_choosing_an_operation_that_needs_a_missing_grid_is_refused():
    with pytest.raises(T.TransformationError) as refused:
        T.resolve("EPSG:4277", "EPSG:4258", operation_id="EPSG:7709")
    assert refused.value.code == "TRANSFORMATION_GRID_MISSING"


def test_the_plan_text_names_the_missing_grids_and_the_blocked_fallback():
    plan = T.plan("EPSG:4277", "EPSG:4258")
    text = " ".join(plan.messages)
    assert "uk_os_OSTN15_NTv2_OSGBtoETRS.tif" in text
    assert "ballpark" in text.lower()


# --- explanations ------------------------------------------------------------------------------------------------------


def test_every_class_has_a_plain_language_explanation_and_accuracy_limits_are_stated():
    assert set(T.KLASS_EXPLANATION) == {"exact", "approximate", "ballpark", "unavailable"}
    assert any("does not include errors in the map sheet" in note for note in T.ACCURACY_NOTES)
    assert any("grids" in note for note in T.ACCURACY_NOTES)


# --- tiles are drawn through the chosen operation -----------------------------------------------------------------------


def _edge_raster(path, crs, x0, y0, size=200, pixel=10.0):
    """A raster whose left half is bright and right half dark, so the position of the edge can be measured."""
    data = np.zeros((size, size), dtype="uint8")
    data[:, : size // 2] = 255
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=size,
        height=size,
        count=1,
        dtype="uint8",
        crs=crs,
        transform=from_origin(x0, y0, pixel, pixel),
    ) as dst:
        dst.write(data, 1)
    return x0 + (size // 2) * pixel, y0 - (size // 2) * pixel  # the edge's easting and the raster's centre northing


def _edge_column(png: bytes) -> float:
    image = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
    row = image[128, :, :]
    drawn = row[:, 3] > 0
    bright = drawn & (row[:, 0] > 128)
    columns = np.flatnonzero(bright)
    return float(columns.max()) if columns.size else float("nan")


def _expected_pixel(operation, easting, northing, z):
    """Where a native point lands in the Web Mercator tile grid when moved through `operation`."""
    lon, lat = operation.transformer.transform(easting, northing)
    x_m, y_m = Transformer.from_crs(WGS84, tiles.WEB_MERCATOR, always_xy=True).transform(lon, lat)
    size = 2 * tiles.ORIGIN / 2**z
    fx, fy = (x_m + tiles.ORIGIN) / size, (tiles.ORIGIN - y_m) / size
    return int(fx), int(fy), (fx - int(fx)) * 256, (fy - int(fy)) * 256


def test_a_tile_follows_the_chosen_datum_shift_and_not_gdals_default(tmp_path):
    crs = "EPSG:24047"  # Indian 1975 / UTM zone 47N: two registered shifts near Thailand
    easting, northing = Transformer.from_crs(WGS84, crs, always_xy=True).transform(100.1, 14.1)
    easting, northing = round(easting / 10) * 10 - 1000, round(northing / 10) * 10 + 1000
    path = tmp_path / "edge.tif"
    edge_e, edge_n = _edge_raster(path, crs, easting, northing)

    z = 19
    assert T.plan(crs, WGS84, THAILAND).status == "selection_required"
    results = {}
    for choice in ("EPSG:1812", "EPSG:1304"):
        shift = T.resolve(crs, WGS84, THAILAND, choice).operation
        pipeline = T.resolve(crs, tiles.WEB_MERCATOR, THAILAND, choice).pipeline
        assert pipeline, "an approximate operation hands GDAL an explicit pipeline"
        results[choice] = (_expected_pixel(shift, edge_e, edge_n, z), pipeline)

    (fine_tile, fine_pipe), (coarse_tile, coarse_pipe) = results["EPSG:1812"], results["EPSG:1304"]
    assert fine_pipe != coarse_pipe
    assert fine_tile[:2] != coarse_tile[:2] or abs(fine_tile[2] - coarse_tile[2]) >= 1.0, (
        "the two shifts must put the edge somewhere visibly different at this zoom"
    )

    # Each tile, drawn through the operation that was chosen, puts the edge where that operation says (about 1 m).
    for (tx, ty, px, _), pipeline in results.values():
        png = tiles.render_tile(path, (1,), (), z, tx, ty, operation=pipeline)
        assert png is not None
        assert _edge_column(png) == pytest.approx(px, abs=4.0)

    # The same tile drawn through the other operation does not have the edge there: the choice is what is drawn.
    tx, ty, px, _ = fine_tile
    other = tiles.render_tile(path, (1,), (), z, tx, ty, operation=coarse_pipe)
    assert other is None or abs(_edge_column(other) - px) > 8 or np.isnan(_edge_column(other))


def test_a_tile_of_an_exact_raster_is_unchanged_by_the_operation_machinery(tmp_path):
    path = tmp_path / "utm.tif"
    easting, northing = Transformer.from_crs(WGS84, "EPSG:32643", always_xy=True).transform(77.2, 28.6)
    edge_e, edge_n = _edge_raster(path, "EPSG:32643", round(easting / 10) * 10 - 1000, round(northing / 10) * 10 + 1000)
    z = 19
    operation = T.resolve("EPSG:32643", WGS84).operation
    tx, ty, expected, _ = _expected_pixel(operation, edge_e, edge_n, z)
    resolved = T.resolve("EPSG:32643", tiles.WEB_MERCATOR)
    assert resolved.pipeline is None
    with_machinery = tiles.render_tile(path, (1,), (), z, tx, ty, operation=resolved.pipeline)
    as_before = tiles.render_tile(path, (1,), (), z, tx, ty)
    assert with_machinery == as_before, "an exact raster is drawn exactly as it was before this change"
    assert _edge_column(with_machinery) == pytest.approx(expected, abs=4.0)
