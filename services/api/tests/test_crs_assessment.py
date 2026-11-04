"""CRS assessment and transformation transparency (OSM/ESM Phase 1), through the API.

All rasters are synthetic GeoTIFFs written in memory. Kalianpur and Indian-subcontinent datums are used only because
the local PROJ registry holds several registered shifts for them; nothing here asserts or validates any Everest-56 or
ESM definition, and no such definition is installed. Positions are checked against PROJ computed independently.
"""

import io

import numpy as np
import pyproj
import pytest
from PIL import Image
from pyproj import Transformer
from rasterio.transform import Affine, from_origin
from sqlalchemy import text

from geopoc.db.session import get_engine
from geopoc.geo import transform as T
from tests import media
from tests.test_assets import upload

WGS84 = "EPSG:4326"
THAILAND = (100.1, 14.1)  # Indian 1975 / UTM 47N: two registered shifts apply here
DELHI = (77.2, 27.1)  # Kalianpur 1962: one registered shift applies here
KALIANPUR_1880_ZONE_I = "EPSG:24371"  # registered projection, but no registered shift to WGS 84: ballpark only
ASSESSMENT = "/api/v1/assets/{id}/crs-assessment"


def geotiff_near(crs, lon, lat, size=(40, 30), pixel=10.0, bands=1):
    """A synthetic GeoTIFF in `crs` whose top-left corner is at (lon, lat), in whole pixels."""
    x, y = Transformer.from_crs(WGS84, crs, always_xy=True).transform(lon, lat)
    transform = from_origin(round(x / pixel) * pixel, round(y / pixel) * pixel, pixel, pixel)
    return media.geotiff_bytes(crs=crs, transform=transform, size=size, bands=bands)


def put(client, project, headers, run_jobs, data, name="scene.tif"):
    response = upload(client, project, headers, name, data, modality="overhead_raster")
    assert response.status_code == 201, response.text
    run_jobs()
    return response.json()["asset"]


def assess(client, asset, **params):
    response = client.get(ASSESSMENT.format(id=asset["id"]), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def active(assessment) -> set[str]:
    return {s["code"] for s in assessment["statuses"] if s["active"]}


def plan_for(assessment, purpose):
    return next(t for t in assessment["transformations"] if t["purpose"] == purpose)


def layers(client, project, *operations):
    response = client.get(f"/api/v1/projects/{project['id']}/layers", params={"operation": list(operations)})
    assert response.status_code == 200, response.text
    return response.json()


def counts():
    with get_engine().connect() as conn:
        return {
            table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            for table in ("asset", "raster_metadata", "georef_assertion", "audit_event", "job")
        }


# --- the assessment itself --------------------------------------------------------------------------------------------


def test_a_utm_raster_is_assessed_with_datum_projection_units_and_exact_transformations(
    client, project, headers, run_jobs
):
    asset = put(client, project, headers, run_jobs, media.geotiff_bytes(crs="EPSG:32643"))
    result = assess(client, asset)

    assert active(result) == {"georeferenced", "crs_identified", "transformation_available"}
    assert [s["code"] for s in result["statuses"]] == [
        "georeferenced",
        "crs_identified",
        "crs_missing",
        "crs_ambiguous",
        "transformation_available",
        "transformation_unavailable",
        "georeferencing_required",
        "validation_failed",
    ]
    crs = result["crs"]
    assert crs["identifier"] == "EPSG:32643"
    assert crs["name"] == "WGS 84 / UTM zone 43N"
    assert crs["kind"] == "projected"
    assert crs["coordinate_units"] == ["metre"]
    assert crs["datum"]["name"].startswith("World Geodetic System 1984")
    assert crs["ellipsoid"]["name"] == "WGS 84"
    assert crs["ellipsoid"]["semi_major_m"] == pytest.approx(6378137.0)
    assert crs["ellipsoid"]["inverse_flattening"] == pytest.approx(298.257223563)
    assert crs["prime_meridian"] == {"name": "Greenwich", "longitude_degrees": 0.0}
    assert crs["projection"]["method"] == "Transverse Mercator"
    parameters = {p["name"]: (p["value"], p["unit"]) for p in crs["projection"]["parameters"]}
    assert parameters["Longitude of natural origin"] == (75.0, "degree")
    assert parameters["Scale factor at natural origin"][0] == pytest.approx(0.9996)
    assert parameters["False easting"] == (500000.0, "metre")
    assert [a["direction"] for a in crs["axes"]] == ["east", "north"]
    assert crs["area_of_use"]["west"] == pytest.approx(72.0)

    for purpose in ("coordinates", "tiles"):
        plan = plan_for(result, purpose)
        assert plan["status"] == "exact"
        assert plan["resolution"]["classification"] == "exact"
        assert plan["resolution"]["accuracy_m"] == 0.0
        assert plan["blocked"] is None
        assert [o["id"] for o in plan["operations"]] == ["datum-unchanged"]
    assert result["raster"]["pixel_units"] == "metre"
    assert result["raster"]["geotransform"] is not None
    assert result["accuracy_notes"] and result["limitations"]
    assert result["versions"]["network"] == "disabled"
    assert "Georeferenced" in result["summary"]


def test_a_wgs84_raster_is_assessed_as_geographic_and_exact(client, project, headers, run_jobs):
    data = media.geotiff_bytes(crs=WGS84, transform=from_origin(77.0, 28.0, 0.001, 0.001))
    asset = put(client, project, headers, run_jobs, data)
    result = assess(client, asset)
    assert active(result) == {"georeferenced", "crs_identified", "transformation_available"}
    assert result["crs"]["kind"] == "geographic"
    assert result["crs"]["projection"] is None
    assert result["crs"]["coordinate_units"] == ["degree"]
    assert plan_for(result, "coordinates")["status"] == "exact"


def test_a_raster_without_a_crs_is_not_given_one(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, media.geotiff_bytes(crs=None))
    result = assess(client, asset)
    assert result["crs"] is None
    assert result["transformations"] == []
    assert active(result) == {"crs_missing", "georeferencing_required"}
    statuses = {s["code"]: s for s in result["statuses"]}
    assert "None is assumed" in statuses["crs_missing"]["message"]
    assert statuses["transformation_available"]["active"] is False
    assert statuses["transformation_unavailable"]["active"] is False  # nothing to transform yet
    # The filename and the "series" never decide the CRS.
    renamed = put(
        client, project, headers, run_jobs, media.geotiff_bytes(crs=None, size=(41, 30)), name="OSM_sheet_43_UTM43N.tif"
    )
    assert assess(client, renamed)["crs"] is None


def test_a_crs_without_a_geotransform_still_needs_georeferencing(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, media.geotiff_bytes(crs="EPSG:32643", transform=None))
    result = assess(client, asset)
    assert active(result) >= {"crs_identified", "georeferencing_required"}
    assert "georeferenced" not in active(result)
    assert result["raster"]["geotransform"] is None


def test_the_assessment_changes_nothing(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, geotiff_near("EPSG:24047", *THAILAND))
    before = counts()
    for _ in range(3):
        assess(client, asset)
        assess(client, asset, operation="EPSG:1812")
    assert counts() == before


def test_only_rasters_have_an_assessment(client, project, headers):
    photo = upload(client, project, headers, "p.jpg", media.image_bytes("JPEG")).json()["asset"]
    assert client.get(ASSESSMENT.format(id=photo["id"])).status_code == 409
    missing = client.get(ASSESSMENT.format(id="00000000-0000-0000-0000-000000000000"))
    assert missing.status_code == 404


# --- approximate: one registered shift, accuracy reported -------------------------------------------------------------


ABIDJAN = (-4.24, 7.69)  # Abidjan 1987 / UTM 30N: one registered shift, documented to 2 m (within the 10 m limit)


def test_an_abidjan_raster_is_approximate_and_says_how_approximate(client, project, headers, run_jobs):
    # Was a Kalianpur raster (999 m). That shift is now refused until acknowledged, so this uses a shift that is within
    # the 10 m limit; the Kalianpur case is in test_transform_acknowledgement.
    asset = put(client, project, headers, run_jobs, geotiff_near("EPSG:2041", *ABIDJAN))
    assert asset["status"] == "validated_with_warnings"
    assert "TRANSFORMATION_APPROXIMATE" in asset["quality_flags"]
    assert "about 2 m" in asset["unknowns"]["transformation_accuracy"]
    assert asset["metadata"]["footprint_transformation"]["operation"]["classification"] == "approximate"
    assert asset["metadata"]["footprint_transformation"]["accuracy_m"] == 2.0

    result = assess(client, asset)
    assert active(result) == {"georeferenced", "crs_identified", "transformation_available"}
    assert result["crs"]["datum"]["name"] == "Abidjan 1987"
    for purpose in ("coordinates", "tiles"):
        plan = plan_for(result, purpose)
        assert plan["status"] == "approximate"
        assert plan["resolution"] == {
            "operation_id": "EPSG:1470",
            "classification": "approximate",
            "accuracy_m": 2.0,
            "chosen_by": "only_option",
            "requires_acknowledgement": False,
            "acknowledged": False,
        }

    [layer] = layers(client, project)["rasters"]
    assert layer["transformation"]["classification"] == "approximate"
    assert layer["transformation"]["accuracy_m"] == 2.0
    assert layer["tile_url"] is not None
    # The outline is where the registered shift puts it: the four corners moved through that operation.
    x, y = Transformer.from_crs(WGS84, "EPSG:2041", always_xy=True).transform(*ABIDJAN)
    x0, y0 = round(x / 10) * 10, round(y / 10) * 10
    shift = T.resolve("EPSG:2041", WGS84, tuple(layer["bounds_4326"]), "EPSG:1470").operation.transformer
    corners = [shift.transform(x0 + dx, y0 - dy) for dx in (0, 400) for dy in (0, 300)]  # 40 x 30 pixels of 10 m
    west, south, east, north = layer["bounds_4326"]
    assert (west, south, east, north) == pytest.approx(
        (
            min(c[0] for c in corners),
            min(c[1] for c in corners),
            max(c[0] for c in corners),
            max(c[1] for c in corners),
        ),
        abs=2e-6,
    )


def test_an_approximate_tile_is_served_without_a_choice(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, geotiff_near("EPSG:2041", *ABIDJAN, size=(200, 200)))
    [layer] = layers(client, project)["rasters"]
    x, y = layer["bounds_4326"][0] + 0.0002, layer["bounds_4326"][3] - 0.0002
    z = 17
    xm, ym = Transformer.from_crs(WGS84, "EPSG:3857", always_xy=True).transform(x, y)
    size = 2 * 20037508.342789244 / 2**z
    tx, ty = int((xm + 20037508.342789244) / size), int((20037508.342789244 - ym) / size)
    response = client.get(f"/api/v1/assets/{asset['id']}/tiles/{z}/{tx}/{ty}.png")
    assert response.status_code == 200
    assert np.asarray(Image.open(io.BytesIO(response.content)))[..., 3].any()


# --- ambiguous: a choice is required ------------------------------------------------------------------------------------


def test_an_ambiguous_raster_asks_for_a_choice_and_places_nothing_by_default(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, geotiff_near("EPSG:24047", *THAILAND))
    assert asset["status"] == "validated_with_warnings"
    assert "TRANSFORMATION_SELECTION_REQUIRED" in asset["quality_flags"]
    assert "indicative" in asset["unknowns"]["transformation"]

    result = assess(client, asset)
    assert active(result) == {"georeferenced", "crs_identified", "transformation_available"}
    plan = plan_for(result, "coordinates")
    assert plan["status"] == "selection_required"
    assert plan["recommended_id"] == "EPSG:1812"
    assert {(o["id"], o["accuracy_m"]) for o in plan["operations"] if o["usable"]} == {
        ("EPSG:1812", 3.0),
        ("EPSG:1304", 5.0),
    }
    assert plan["resolution"] is None
    assert plan["blocked"]["code"] == "TRANSFORMATION_SELECTION_REQUIRED"

    [layer] = layers(client, project)["rasters"]
    assert layer["transformation"]["selection_required"] is True
    assert layer["tile_url"] is None
    assert "Choose" in layer["display_unavailable_reason"]
    assert {c["id"] for c in layer["transformation"]["candidates"]} == {"EPSG:1812", "EPSG:1304"}

    refused = client.get(f"/api/v1/assets/{asset['id']}/tiles/10/800/480.png")
    assert refused.status_code == 409
    assert refused.json()["code"] == "TRANSFORMATION_SELECTION_REQUIRED"
    assert {e["field"] for e in refused.json()["errors"]} >= {"EPSG:1812", "EPSG:1304"}


def test_an_explicit_choice_places_the_raster_and_is_what_is_drawn(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, geotiff_near("EPSG:24047", *THAILAND, size=(100, 100)))
    chosen = f"{asset['id']}:EPSG:1304"
    [layer] = layers(client, project, chosen)["rasters"]
    assert layer["transformation"]["classification"] == "approximate"
    assert layer["transformation"]["operation_id"] == "EPSG:1304"
    assert layer["transformation"]["chosen_by"] == "selection"
    assert layer["transformation"]["accuracy_m"] == 5.0
    assert layer["tile_url"].endswith("&operation=EPSG:1304")

    other = layers(client, project, f"{asset['id']}:EPSG:1812")["rasters"][0]
    assert other["transformation"]["operation_id"] == "EPSG:1812"
    assert other["bounds_4326"] != layer["bounds_4326"], "the outline follows the chosen shift"

    unknown = layers(client, project, f"{asset['id']}:EPSG:9999")["rasters"][0]
    assert unknown["transformation"]["selection_required"] is True  # a stale choice is ignored, not applied

    west, south, east, north = layer["bounds_4326"]
    centre_lon, centre_lat = (west + east) / 2, (south + north) / 2
    z = 15
    xm, ym = Transformer.from_crs(WGS84, "EPSG:3857", always_xy=True).transform(centre_lon, centre_lat)
    size = 2 * 20037508.342789244 / 2**z
    tx, ty = int((xm + 20037508.342789244) / size), int((20037508.342789244 - ym) / size)
    served = client.get(layer["tile_url"].format(z=z, x=tx, y=ty))
    assert served.status_code == 200
    bad = client.get(f"/api/v1/assets/{asset['id']}/tiles/{z}/{tx}/{ty}.png", params={"operation": "EPSG:9999"})
    assert (bad.status_code, bad.json()["code"]) == (422, "TRANSFORMATION_UNKNOWN_OPERATION")


# --- ballpark: blocked ----------------------------------------------------------------------------------------------------


def test_a_ballpark_raster_is_not_placed_and_says_why(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, geotiff_near(KALIANPUR_1880_ZONE_I, *DELHI))
    assert asset["status"] == "validated_with_warnings"
    assert {"TRANSFORMATION_BLOCKED", "TRANSFORMATION_BALLPARK_BLOCKED", "NOT_GEOREFERENCED"} <= set(
        asset["quality_flags"]
    )
    assert asset["raster"]["georeferenced"] is False
    assert "ballpark" in asset["unknowns"]["transformation"].lower()

    result = assess(client, asset)
    assert active(result) == {
        "crs_identified",
        "transformation_unavailable",
        "georeferencing_required",
        "validation_failed",
    }
    assert result["crs"]["identifier"] == KALIANPUR_1880_ZONE_I
    assert result["crs"]["projection"]["method"] == "Lambert Conic Conformal (1SP)"
    assert result["crs"]["coordinate_units"] == ["Indian yard"]
    assert result["crs"]["datum"]["name"] == "Kalianpur 1880"
    plan = plan_for(result, "coordinates")
    assert plan["status"] == "unavailable"
    assert plan["blocked"]["code"] == "TRANSFORMATION_BALLPARK_BLOCKED"
    assert [o["classification"] for o in plan["operations"]] == ["ballpark"]
    assert plan["operations"][0]["usable"] is False
    assert plan["operations"][0]["accuracy_m"] is None

    body = layers(client, project)
    assert body["rasters"] == []
    assert [e["asset_id"] for e in body["excluded"]] == [asset["id"]]
    assert client.get(f"/api/v1/assets/{asset['id']}/tiles/10/800/480.png").status_code == 409


def test_a_custom_crs_with_no_datum_shift_is_not_silently_placed(client, project, headers, run_jobs):
    # A projected CRS written without a datum: PROJ can only fall back to a ballpark offset.
    crs = pyproj.CRS.from_proj4("+proj=utm +zone=43 +ellps=WGS84 +units=m +no_defs")
    data = media.geotiff_bytes(crs=crs, transform=from_origin(500000, 3000000, 10, 10))
    asset = put(client, project, headers, run_jobs, data)
    if asset["raster"]["crs_status"] != "valid":
        pytest.skip("the GeoTIFF writer stored a named datum; the registered-CRS ballpark test above covers this")
    result = assess(client, asset)
    plan = plan_for(result, "coordinates")
    if plan["status"] == "exact":
        pytest.skip("this PROJ build treats a WGS 84 ellipsoid with no datum as WGS 84")
    assert plan["status"] == "unavailable"
    assert "TRANSFORMATION_BLOCKED" in asset["quality_flags"]
    assert asset["raster"]["georeferenced"] is False


# --- point conversion --------------------------------------------------------------------------------------------------


def convert(client, source, target, x, y, **params):
    return client.get("/api/v1/crs/transform", params={"source": source, "target": target, "x": x, "y": y, **params})


def test_known_wgs84_and_utm_conversions_are_exact_and_reported_as_such(client):
    forward = convert(client, "EPSG:32643", WGS84, 500000, 3000000)
    assert forward.status_code == 200
    body = forward.json()
    assert body["x"] == pytest.approx(75.0, abs=1e-9)
    assert body["y"] == pytest.approx(27.12246964, abs=1e-8)
    assert body["operation"]["classification"] == "exact"
    assert body["operation"]["accuracy_m"] == 0.0
    assert body["transformation"]["classification"] == "exact"
    assert body["warnings"] == []

    back = convert(client, WGS84, "EPSG:32643", body["x"], body["y"]).json()
    assert (back["x"], back["y"]) == pytest.approx((500000.0, 3000000.0), abs=1e-4)
    reference = Transformer.from_crs(WGS84, "EPSG:32643", always_xy=True).transform(75.0, 27.12246964)
    assert (back["x"], back["y"]) == pytest.approx(reference, abs=1e-3)


def test_a_point_conversion_reports_an_approximate_shift_with_its_accuracy(client):
    x, y = Transformer.from_crs(WGS84, "EPSG:2041", always_xy=True).transform(*ABIDJAN)
    response = convert(client, "EPSG:2041", WGS84, x, y)
    assert response.status_code == 200
    body = response.json()
    assert body["operation"]["id"] == "EPSG:1470"
    assert body["operation"]["classification"] == "approximate"
    assert body["operation"]["accuracy_m"] == 2.0 and body["operation"]["requires_acknowledgement"] is False
    assert "APPROXIMATE_TRANSFORMATION" in body["warnings"]
    assert body["transformation"]["accuracy_m"] == 2.0


def test_a_point_conversion_with_a_shift_worse_than_ten_metres_needs_acknowledgement(client):
    x, y = Transformer.from_crs(WGS84, "EPSG:24313", always_xy=True).transform(*DELHI)
    refused = convert(client, "EPSG:24313", WGS84, x, y)
    assert refused.status_code == 409 and refused.json()["code"] == "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED"
    context = refused.json()["context"]
    assert (context["operation_id"], context["accuracy_m"], context["threshold_m"]) == ("EPSG:1247", 999.0, 10.0)
    accepted = convert(
        client, "EPSG:24313", WGS84, x, y, acknowledge_operation="EPSG:1247", acknowledge_accuracy_m=999.0
    ).json()
    assert accepted["operation"]["accuracy_m"] == 999.0 and "APPROXIMATE_TRANSFORMATION" in accepted["warnings"]


def test_a_point_conversion_with_several_applicable_shifts_needs_a_choice(client):
    x, y = Transformer.from_crs(WGS84, "EPSG:24047", always_xy=True).transform(*THAILAND)
    refused = convert(client, "EPSG:24047", WGS84, x, y)
    assert refused.status_code == 409
    assert refused.json()["code"] == "TRANSFORMATION_SELECTION_REQUIRED"
    assert {e["field"] for e in refused.json()["errors"]} == {"EPSG:1812", "EPSG:1304"}

    fine = convert(client, "EPSG:24047", WGS84, x, y, operation="EPSG:1812").json()
    coarse = convert(client, "EPSG:24047", WGS84, x, y, operation="EPSG:1304").json()
    assert fine["operation"]["accuracy_m"] == 3.0 and coarse["operation"]["accuracy_m"] == 5.0
    assert (fine["x"], fine["y"]) != (coarse["x"], coarse["y"])
    unknown = convert(client, "EPSG:24047", WGS84, x, y, operation="EPSG:9999")
    assert (unknown.status_code, unknown.json()["code"]) == (422, "TRANSFORMATION_UNKNOWN_OPERATION")


def test_a_ballpark_point_conversion_is_blocked_unless_explicitly_accepted(client):
    x, y = Transformer.from_crs(WGS84, KALIANPUR_1880_ZONE_I, always_xy=True).transform(*DELHI)
    refused = convert(client, KALIANPUR_1880_ZONE_I, WGS84, x, y)
    assert (refused.status_code, refused.json()["code"]) == (409, "TRANSFORMATION_BALLPARK_BLOCKED")

    accepted = convert(client, KALIANPUR_1880_ZONE_I, WGS84, x, y, accept_ballpark=True)
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["operation"]["classification"] == "ballpark"
    assert "BALLPARK_ACCEPTED" in body["warnings"] and "ACCURACY_NOT_STATED" in body["warnings"]
    assert body["operation"]["accuracy_m"] is None


def test_a_point_conversion_that_needs_a_missing_grid_names_the_grid(client):
    refused = convert(client, "EPSG:4277", "EPSG:4258", -1.5, 52.0)  # OSGB36 to ETRS89 needs the OSTN15 grid
    assert refused.status_code == 409
    body = refused.json()
    assert body["code"] == "TRANSFORMATION_GRID_MISSING"
    assert "uk_os_OSTN15_NTv2_OSGBtoETRS.tif" in body["detail"]
    assert "Nothing was downloaded" in body["detail"]


def test_incomplete_crs_names_are_still_refused(client):
    assert convert(client, "WGS84 + LCC", WGS84, 1, 2).status_code == 422
    assert client.get("/api/v1/crs/operations", params={"source": "WGS84 + LCC", "target": WGS84}).status_code == 422


# --- the list of operations ------------------------------------------------------------------------------------------------


def test_the_operations_list_classifies_every_candidate(client):
    body = client.get("/api/v1/crs/operations", params={"source": "EPSG:4145", "target": WGS84}).json()
    plan = body["plan"]
    assert plan["status"] == "selection_required"
    by_class = {}
    for operation in plan["operations"]:
        by_class.setdefault(operation["classification"], []).append(operation)
    assert len(by_class["approximate"]) >= 2 and len(by_class["ballpark"]) == 1
    assert all(o["accuracy_m"] is not None for o in by_class["approximate"])
    assert by_class["ballpark"][0]["usable"] is False
    assert set(body["class_explanations"]) == {"exact", "approximate", "ballpark", "unavailable"}
    assert body["versions"]["network"] == "disabled"

    narrowed = client.get(
        "/api/v1/crs/operations",
        params={"source": "EPSG:4145", "target": WGS84, "west": 77, "south": 27, "east": 77.5, "north": 27.5},
    ).json()
    assert narrowed["plan"]["status"] == "approximate"
    assert narrowed["area_used"] == {"west": 77.0, "south": 27.0, "east": 77.5, "north": 27.5}


def test_the_operations_list_reports_missing_grids(client):
    plan = client.get("/api/v1/crs/operations", params={"source": "EPSG:4277", "target": "EPSG:4258"}).json()["plan"]
    assert plan["status"] == "unavailable"
    assert plan["missing_grids"] == ["uk_os_OSTN15_NTv2_OSGBtoETRS.tif"]
    missing = [o for o in plan["operations"] if o["classification"] == "unavailable"]
    assert missing[0]["grids"] == [{"name": "uk_os_OSTN15_NTv2_OSGBtoETRS.tif", "available": False}]
    assert "not installed" in missing[0]["summary"]


@pytest.mark.parametrize(
    "params",
    [
        {"west": 1},
        {"west": 10, "south": 0, "east": 5, "north": 5},
        {"west": 0, "south": 10, "east": 5, "north": 5},
        {"west": -200, "south": 0, "east": 5, "north": 5},
    ],
)
def test_the_operations_area_must_be_complete_and_sensible(client, params):
    response = client.get("/api/v1/crs/operations", params={"source": "EPSG:4145", "target": WGS84, **params})
    assert response.status_code == 422


# --- recording georeferencing with a datum choice --------------------------------------------------------------------------


def unreferenced(client, project, headers, run_jobs, lon, lat):
    """A raster with a geotransform in the right place but no CRS."""
    x, y = Transformer.from_crs(WGS84, "EPSG:24047", always_xy=True).transform(lon, lat)
    data = media.geotiff_bytes(
        crs=None, transform=Affine(10, 0, round(x / 10) * 10, 0, -10, round(y / 10) * 10), size=(80, 80)
    )
    return put(client, project, headers, run_jobs, data)


def test_recording_a_crs_that_needs_a_datum_choice_requires_the_choice(client, project, headers, run_jobs):
    asset = unreferenced(client, project, headers, run_jobs, *THAILAND)
    path = f"/api/v1/assets/{asset['id']}/georeferencing"
    base = {"operation": "assign_crs", "crs_id": "EPSG:24047", "reason": "Stated in the supplier's delivery note."}

    without = client.post(f"{path}/validate", json=base).json()
    assert without["valid"] is False
    plan = without["transformation_plan"]
    assert plan["status"] == "selection_required"
    assert {o["id"] for o in plan["operations"] if o["usable"]} == {"EPSG:1812", "EPSG:1304"}
    failed = [c for c in without["checks"] if c["status"] == "failed"]
    assert [c["code"] for c in failed] == ["TRANSFORMATION_AVAILABLE"]
    assert "Choose" in failed[0]["message"] and "EPSG:1812" in failed[0]["message"]

    refused = client.post(path, json={**base, "expected_version": 0, "confirm": True}, headers=headers)
    assert refused.status_code == 422 and refused.json()["code"] == "GEOREFERENCE_INVALID"
    assert client.get(path).json()["history"] == []  # nothing was recorded

    chosen = client.post(f"{path}/validate", json={**base, "transformation_operation": "EPSG:1812"}).json()
    assert chosen["valid"] is True
    assert chosen["transformation_plan"] is None
    available = next(c for c in chosen["checks"] if c["code"] == "TRANSFORMATION_AVAILABLE")
    assert available["status"] == "warning" and "3" in available["message"] and "EPSG:1812" in available["message"]

    recorded = client.post(
        path,
        json={**base, "transformation_operation": "EPSG:1812", "expected_version": 0, "confirm": True},
        headers=headers,
    )
    assert recorded.status_code == 201, recorded.text
    version = recorded.json()["history"][0]
    assert version["validation"]["transformation"]["operation"]["id"] == "EPSG:1812"
    assert version["validation"]["transformation"]["accuracy_m"] == 3.0

    [layer] = layers(client, project)["rasters"]
    assert layer["placement"] == "user_asserted"
    assert layer["transformation"]["operation_id"] == "EPSG:1812"
    assert layer["transformation"]["chosen_by"] == "recorded"
    assert layer["tile_url"] is not None, "a recorded choice is kept, so no choice is asked for again"
    west, south, east, north = layer["bounds_4326"]
    assert client.get(f"/api/v1/assets/{asset['id']}/tiles/10/800/480.png").status_code in (200, 204)


def test_recording_a_crs_that_only_has_a_ballpark_transformation_is_refused(client, project, headers, run_jobs):
    asset = unreferenced(client, project, headers, run_jobs, *DELHI)
    path = f"/api/v1/assets/{asset['id']}/georeferencing"
    body = {"operation": "assign_crs", "crs_id": KALIANPUR_1880_ZONE_I, "reason": "Stated in the delivery note."}
    validation = client.post(f"{path}/validate", json=body).json()
    assert validation["valid"] is False
    assert validation["transformation_plan"]["status"] == "unavailable"
    failed = next(
        c for c in validation["checks"] if c["status"] == "failed" and c["code"] == "TRANSFORMATION_AVAILABLE"
    )
    assert "ballpark" in failed["message"].lower()
    refused = client.post(path, json={**body, "expected_version": 0, "confirm": True}, headers=headers)
    assert refused.status_code == 422
    assert client.get(path).json()["history"] == []


def test_recording_a_crs_with_one_registered_shift_needs_no_choice(client, project, headers, run_jobs):
    x, y = Transformer.from_crs(WGS84, "EPSG:2041", always_xy=True).transform(*ABIDJAN)
    data = media.geotiff_bytes(
        crs=None, transform=Affine(10, 0, round(x / 10) * 10, 0, -10, round(y / 10) * 10), size=(80, 80)
    )
    asset = put(client, project, headers, run_jobs, data)
    body = {"operation": "assign_crs", "crs_id": "EPSG:2041", "reason": "Stated in the delivery note."}
    validation = client.post(f"/api/v1/assets/{asset['id']}/georeferencing/validate", json=body).json()
    assert validation["valid"] is True
    available = next(c for c in validation["checks"] if c["code"] == "TRANSFORMATION_AVAILABLE")
    assert available["status"] == "warning" and "about 2 m" in available["message"]


def test_recording_a_crs_with_a_shift_worse_than_ten_metres_needs_the_acknowledgement(
    client, project, headers, run_jobs
):
    x, y = Transformer.from_crs(WGS84, "EPSG:24313", always_xy=True).transform(*DELHI)
    data = media.geotiff_bytes(
        crs=None, transform=Affine(10, 0, round(x / 10) * 10, 0, -10, round(y / 10) * 10), size=(80, 80)
    )
    asset = put(client, project, headers, run_jobs, data)
    path = f"/api/v1/assets/{asset['id']}/georeferencing/validate"
    body = {"operation": "assign_crs", "crs_id": "EPSG:24313", "reason": "Stated in the delivery note."}
    refused = client.post(path, json=body).json()
    assert refused["valid"] is False
    assert (
        next(c for c in refused["checks"] if c["code"] == "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED")["status"]
        == "failed"
    )
    accepted = client.post(path, json={**body, "acknowledge_transformation": True}).json()
    assert accepted["valid"] is True
    available = next(c for c in accepted["checks"] if c["code"] == "TRANSFORMATION_AVAILABLE")
    assert available["status"] == "warning" and "999" in available["message"]


# --- regression: exact rasters behave as before ------------------------------------------------------------------------------


def test_an_exact_raster_is_still_placed_without_any_warning(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, media.geotiff_bytes(crs="EPSG:32643"))
    assert asset["status"] == "validated"
    assert not {f for f in asset["quality_flags"] if f.startswith("TRANSFORMATION")}
    transformation = asset["metadata"]["footprint_transformation"]
    assert transformation["target_crs_id"] == WGS84
    assert transformation["accuracy_m"] == 0.0
    assert transformation["operation"]["id"] == "datum-unchanged"
    [layer] = layers(client, project)["rasters"]
    assert layer["placement"] == "trusted"
    assert layer["transformation"]["status"] == "exact"
    assert layer["tile_url"].endswith("?v=0")  # unchanged address, so cached tiles stay valid
