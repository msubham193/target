"""Map-sheet metadata assessment (OSM and ESM), through the API.

Synthetic rasters only. Kalianpur 1880 zone I (EPSG:24371) is used because the local registry holds it with an Everest
ellipsoid, a Lambert Conic Conformal projection and no usable datum shift: a stand-in for the kind of definition the
client describes, NOT an ESM definition. No Everest-56 or ESM CRS is installed or implied, and nothing here shows that real
ESM sheets are supported: what is checked is that nothing is invented and that the gaps are reported.
"""

import pytest

from tests import media
from tests.test_assets import rows
from tests.test_crs_assessment import DELHI, KALIANPUR_1880_ZONE_I, geotiff_near
from tests.test_map_sheet_formats import pdf_bytes
from tests.test_map_sheets import layers, put

ASSESS = "/api/v1/assets/{id}/map-sheet"
NOTES = "/api/v1/assets/{id}/map-sheet/metadata-notes"


def assessment(client, asset):
    response = client.get(ASSESS.format(id=asset["id"]))
    assert response.status_code == 200, response.text
    return response.json()


def fact(result, field):
    return next(f for f in result["facts"] if f["field"] == field)


def unresolved(result):
    return {(u["field"], u["status"]) for u in result["unresolved"]}


# --- what a georeferenced sheet states --------------------------------------------------------------------------------


def test_a_georeferenced_osm_sheet_reports_what_its_file_states(client, project, headers, run_jobs):
    asset = put(
        client,
        project,
        headers,
        run_jobs,
        "osm.tif",
        media.geotiff_bytes(crs="EPSG:32643", size=(100, 80)),
        series="osm",
    )["asset"]
    result = assessment(client, asset)

    datum, ellipsoid = fact(result, "datum"), fact(result, "ellipsoid")
    assert datum["state"] == "stated_by_file" and "World Geodetic System 1984" in datum["value"]["name"]
    assert ellipsoid["value"]["name"] == "WGS 84" and ellipsoid["value"]["semi_major_m"] == pytest.approx(6378137.0)
    assert fact(result, "prime_meridian")["value"]["name"] == "Greenwich"
    projection = fact(result, "projection")
    assert projection["value"]["method"] == "Transverse Mercator"
    names = {p["name"] for p in fact(result, "projection_parameters")["value"]}
    assert {"Latitude of natural origin", "Longitude of natural origin", "Scale factor at natural origin"} <= names
    assert fact(result, "units")["value"] == ["metre"]
    grid = fact(result, "coordinate_grid")
    assert grid["value"]["kind"] == "projected" and "not read" in grid["detail"]
    extent = fact(result, "extent")
    assert extent["state"] == "stated_by_file" and extent["value"]["area_km2"] == pytest.approx(
        0.8, rel=0.01
    )  # 1000 m x 800 m
    transformation = fact(result, "transformation_to_wgs84")
    assert transformation["state"] == "from_registry" and transformation["value"]["status"] == "exact"

    assert result["unresolved"] == [] and result["family_hints"] == []
    assert result["info"]["declaration"]["series"] == "osm" and "label" in result["series_note"]
    assert result["crs_assessment"]["crs"]["identifier"] == "EPSG:32643"


# --- what an unplaced sheet does not state ----------------------------------------------------------------------------


def test_an_esm_scan_without_a_crs_reports_everything_unknown_and_what_is_needed(client, project, headers, run_jobs):
    data = media.image_bytes("PNG", size=(300, 200))
    asset = put(client, project, headers, run_jobs, "ESM_Everest1956_polyconic_NH44-1.png", data, series="esm")["asset"]
    result = assessment(client, asset)

    for field in ("datum", "ellipsoid", "projection", "projection_parameters", "units", "coordinate_grid"):
        assert fact(result, field)["state"] == "unknown", field
    assert fact(result, "extent")["state"] == "unknown" and fact(result, "extent")["value"] == {"pixels": [300, 200]}
    assert {
        ("datum", "missing"),
        ("ellipsoid", "missing"),
        ("projection", "missing"),
        ("units", "missing"),
    } <= unresolved(result)
    assert ("transformation_to_wgs84", "missing") in unresolved(result)
    assert ("projection_parameters", "declared_unverified") in unresolved(result)  # ESM sheets are not assumed alike
    assert result["family_hints"] == []  # nothing in the file names a family; the file name is not read for one

    needed = " ".join(result["required_information"])
    assert "authoritative written definition" in needed and "Real sample sheets" in needed
    assert "not assumed equal" in needed
    assert "names, not definitions" in result["series_note"]

    # Nothing was assigned: no CRS on the raster, no georeferencing version, no custom CRS registered.
    raster = client.get(f"/api/v1/assets/{asset['id']}").json()["raster"]
    assert raster["crs_status"] == "missing" and raster["crs_identifier"] is None
    assert client.get(f"/api/v1/assets/{asset['id']}/georeferencing").json()["current"]["version"] == 0
    assert (
        rows("SELECT count(*) FROM crs_definition")[0][0] == 0
        and rows("SELECT count(*) FROM georef_assertion")[0][0] == 0
    )
    assert "ESM" not in str(raster)


# --- an Everest / LCC family CRS in the file --------------------------------------------------------------------------


def test_a_sheet_in_an_everest_family_crs_is_not_placed_and_needs_an_authoritative_definition(
    client, project, headers, run_jobs
):
    data = geotiff_near(KALIANPUR_1880_ZONE_I, *DELHI)
    body = put(client, project, headers, run_jobs, "esm-like.tif", data, series="esm", sheet_label="X")
    asset = body["asset"]
    assert asset["raster"]["georeferenced"] is False and "TRANSFORMATION_BLOCKED" in asset["quality_flags"]
    assert layers(client, project)["rasters"] == []  # never placed through a ballpark fallback

    result = assessment(client, asset)
    families = {h["family"] for h in result["family_hints"]}
    assert {"Lambert Conic Conformal", "Indian / Kalianpur datums"} <= families
    matched = " ".join(h["matched"] for h in result["family_hints"])
    assert "Kalianpur 1880" in matched
    # The file states a CRS (so these are facts), but the sheet cannot be moved to WGS 84 with an authoritative shift.
    assert (
        fact(result, "datum")["state"] == "stated_by_file"
        and fact(result, "datum")["value"]["name"] == "Kalianpur 1880"
    )
    assert fact(result, "projection")["value"]["method"].startswith("Lambert Conic Conformal")
    assert fact(result, "units")["value"] == ["Indian yard"]
    assert ("datum", "needs_authority") in unresolved(result)
    assert ("transformation_to_wgs84", "needs_authority") in unresolved(result)
    ballpark = next(u for u in result["unresolved"] if u["field"] == "transformation_to_wgs84")
    assert "ballpark" in ballpark["detail"].lower() and "blocked" in ballpark["required"].lower()
    plan = next(t for t in result["crs_assessment"]["transformations"] if t["purpose"] == "coordinates")
    assert plan["status"] == "unavailable" and [o["classification"] for o in plan["operations"]] == ["ballpark"]
    assert "authoritative" in " ".join(result["required_information"])
    assert any("Real sample sheets" in line for line in result["required_information"])  # ESM list added

    # No Everest-56 anything is offered anywhere in the answer.
    assert "1956" not in str([h["matched"] for h in result["family_hints"]])
    assert all("1956" not in o["name"] for t in result["crs_assessment"]["transformations"] for o in t["operations"])


# --- recording unresolved metadata ------------------------------------------------------------------------------------


def test_a_note_about_unresolved_metadata_is_recorded_but_never_applied(client, project, headers, run_jobs, profile):
    data = media.image_bytes("PNG", size=(300, 200))
    asset = put(client, project, headers, run_jobs, "esm.png", data, series="esm")["asset"]
    payload = {
        "field": "datum",
        "value": "Everest 1956 (as printed in the sheet margin)",
        "source": "Sheet margin text, photographed by the reviewer",
        "status": "needs_authority",
    }
    anonymous = client.post(NOTES.format(id=asset["id"]), json=payload)
    assert anonymous.status_code == 401

    recorded = client.post(NOTES.format(id=asset["id"]), json=payload, headers=headers)
    assert recorded.status_code == 201, recorded.text
    note = recorded.json()
    assert note["field"] == "datum" and note["status"] == "needs_authority" and note["applied"] is False
    assert note["recorded_by"] == profile["display_name"] and note["source"].startswith("Sheet margin")

    result = assessment(client, asset)
    assert [n["id"] for n in result["notes"]] == [note["id"]]
    assert fact(result, "datum")["state"] == "unknown"  # a note does not turn into a fact
    assert ("datum", "missing") in unresolved(result)

    # It changed nothing about the sheet.
    assert client.get(f"/api/v1/assets/{asset['id']}").json()["raster"]["crs_status"] == "missing"
    assert client.get(f"/api/v1/assets/{asset['id']}/georeferencing").json()["current"]["version"] == 0
    assert (
        rows("SELECT count(*) FROM crs_definition")[0][0] == 0
        and rows("SELECT count(*) FROM georef_assertion")[0][0] == 0
    )
    (details,) = [r[0] for r in rows("SELECT details FROM audit_event WHERE action = 'map_sheet.metadata_note'")]
    assert details["value"].startswith("Everest 1956") and details["source"]


def test_notes_need_a_source_a_known_field_and_a_map_sheet(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, "s.png", media.image_bytes("PNG"), series="esm")["asset"]
    url = NOTES.format(id=asset["id"])
    base = {"field": "projection", "value": "Polyconic", "source": "margin"}
    assert client.post(url, json={**base, "source": ""}, headers=headers).status_code == 422
    assert client.post(url, json={**base, "value": " "}, headers=headers).status_code == 422
    assert client.post(url, json={**base, "field": "crs"}, headers=headers).status_code == 422
    assert client.post(url, json={**base, "status": "verified"}, headers=headers).status_code == 422
    plain = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        params={"modality": "overhead_raster", "filename": "plain.tif"},
        content=media.geotiff_bytes(),
        headers=headers,
    ).json()["asset"]
    refused = client.post(NOTES.format(id=plain["id"]), json=base, headers=headers)
    assert refused.status_code == 409 and refused.json()["code"] == "NOT_A_MAP_SHEET"
    assert client.get(ASSESS.format(id=plain["id"])).status_code == 409
    assert rows("SELECT count(*) FROM audit_event WHERE action = 'map_sheet.metadata_note'")[0][0] == 0


def test_a_pdf_sheet_reports_that_nothing_could_be_read_as_a_crs(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, "esm.pdf", pdf_bytes(geo=True), series="esm")["asset"]
    result = assessment(client, asset)
    assert result["pdf"]["page_count"] == 1 and result["crs_assessment"] is None
    assert {("datum", "missing"), ("transformation_to_wgs84", "missing")} <= unresolved(result)
    assert fact(result, "datum")["state"] == "unknown"
    assert result["pdf"]["declared_epsg"] == 32643  # shown as unverified evidence only
    assert "not read" in " ".join(result["limitations"]).lower() or "does not read" in " ".join(result["limitations"])


def test_the_assessment_is_read_only(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, "ro.tif", media.geotiff_bytes(crs=None), series="osm")["asset"]
    before = [
        rows(f"SELECT count(*) FROM {t}")[0][0]
        for t in ("asset", "raster_metadata", "georef_assertion", "audit_event", "job")
    ]
    assessment(client, asset)
    assessment(client, asset)
    after = [
        rows(f"SELECT count(*) FROM {t}")[0][0]
        for t in ("asset", "raster_metadata", "georef_assertion", "audit_event", "job")
    ]
    assert before == after
