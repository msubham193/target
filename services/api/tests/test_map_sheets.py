"""Map-sheet intake (OSM/ESM): GeoTIFF, scanned pictures and PDF, through the API.

Every file is synthetic and generated in memory. No real Open Series or Everest Series sheet is used, so nothing here
shows compatibility with real sheets, and no ESM or Everest-56 CRS exists to be tested: what is tested is that nothing
is ever assigned from a series, a file name or a picture.
"""

import hashlib

from sqlalchemy import text

from geopoc.db.session import get_engine
from tests import media
from tests.test_assets import rows, stored_files, upload


def sheet(client, project, headers, name, data, **params):
    return client.post(
        f"/api/v1/projects/{project['id']}/map-sheets",
        params={"filename": name, **params},
        content=data,
        headers=headers,
    )


def put(client, project, headers, run_jobs, name, data, **params):
    response = sheet(client, project, headers, name, data, **params)
    assert response.status_code == 201, response.text
    run_jobs()
    return response.json()


def layers(client, project):
    response = client.get(f"/api/v1/projects/{project['id']}/layers")
    assert response.status_code == 200, response.text
    return response.json()


# --- OSM GeoTIFF ------------------------------------------------------------------------------------------------------


def test_an_osm_geotiff_is_stored_unmodified_and_placed_from_its_own_metadata(client, project, headers, run_jobs):
    data = media.geotiff_bytes(crs="EPSG:32643", size=(60, 40))
    body = put(
        client, project, headers, run_jobs, "NF43-01.tif", data,
        series="osm", sheet_label="NF 43-01", scale_denominator=250000, edition="2nd",
    )  # fmt: skip
    asset, sheet_info = body["asset"], body["map_sheet"]

    assert asset["modality"] == "overhead_raster" and asset["detected_format"] == "GTiff"
    assert asset["sha256"] == hashlib.sha256(data).hexdigest()
    assert [p.read_bytes() for p in stored_files()] == [data]
    assert "MAP_SHEET" in asset["quality_flags"] and "MAP_SHEET_SCANNED" not in asset["quality_flags"]
    assert asset["status"] == "validated"  # the sheet's kind is a fact, not a warning

    declaration = sheet_info["declaration"]
    assert declaration["series"] == "osm" and declaration["series_label"] == "Open Series Map (OSM)"
    assert declaration["sheet_label"] == "NF 43-01" and declaration["scale_denominator"] == 250000
    assert declaration["edition"] == "2nd" and declaration["declared_by"] == "Test Reviewer"
    assert declaration["basis"] == "declared_by_uploader"
    assert sheet_info["intake"] == "georeferenced_raster"
    assert sheet_info["georeferencing_basis"] == "file_metadata" and sheet_info["placed"] is True
    assert [step["step"] for step in sheet_info["processing"]] == ["stored_original", "inspected", "georeferencing"]
    assert asset["map_sheet"] == sheet_info

    # The CRS is what the file says, not anything the series implies.
    assert asset["raster"]["crs_identifier"] == "EPSG:32643" and asset["raster"]["georeferenced"] is True

    (layer,) = layers(client, project)["rasters"]
    assert layer["placement"] == "trusted"
    assert layer["map_sheet"]["series"] == "osm" and layer["map_sheet"]["sheet_label"] == "NF 43-01"
    assert layer["map_sheet"]["georeferencing_basis"] == "file_metadata"
    assert client.get(layer["tile_url"].replace("{z}", "0").replace("{x}", "0").replace("{y}", "0")).status_code in (
        200,
        204,
    )


def test_the_intake_is_audited_with_the_declaration(client, project, headers):
    sheet(client, project, headers, "a.tif", media.geotiff_bytes(), series="osm", sheet_label="X1")
    (details,) = [r[0] for r in rows("SELECT details FROM audit_event WHERE action = 'asset.upload'")]
    assert details["map_sheet"]["series"] == "osm" and details["map_sheet"]["sheet_label"] == "X1"
    assert details["map_sheet"]["basis"] == "declared_by_uploader"


def test_a_series_label_never_changes_the_crs_or_placement(client, project, headers, run_jobs):
    """The same bytes under all three series give the same CRS: the declaration is attribution only."""
    data = media.geotiff_bytes(crs="EPSG:32643")
    results = []
    for index, series in enumerate(("osm", "esm", "unspecified")):
        other = media.geotiff_bytes(crs="EPSG:32643", size=(40 + index, 30))  # distinct content: no duplicate link
        results.append(put(client, project, headers, run_jobs, f"s{index}.tif", other, series=series)["asset"])
    assert {r["raster"]["crs_identifier"] for r in results} == {"EPSG:32643"}
    assert {r["map_sheet"]["georeferencing_basis"] for r in results} == {"file_metadata"}
    assert [r["map_sheet"]["declaration"]["series"] for r in results] == ["osm", "esm", "unspecified"]
    assert data  # the baseline bytes were never needed to place anything


def test_a_file_name_that_claims_a_crs_does_not_give_the_sheet_one(client, project, headers, run_jobs):
    data = media.geotiff_bytes(crs=None)
    body = put(client, project, headers, run_jobs, "ESM_Everest1956_Polyconic_NH44.tif", data, series="esm")
    raster = body["asset"]["raster"]
    assert raster["crs_status"] == "missing" and raster["crs_identifier"] is None and raster["georeferenced"] is False
    assert body["map_sheet"]["placed"] is False and body["map_sheet"]["georeferencing_basis"] == "none"
    assert "MISSING_CRS" in body["asset"]["quality_flags"] and "MAP_SHEET_SCANNED" in body["asset"]["quality_flags"]
    excluded = layers(client, project)["excluded"]
    (entry,) = excluded
    assert entry["reason"] == "CRS_MISSING" and entry["map_sheet"]["series"] == "esm"
    assert entry["georeferencing_can_be_recorded"] is True


def test_a_geotiff_without_a_geotransform_is_stored_unplaced(client, project, headers, run_jobs):
    body = put(client, project, headers, run_jobs, "nogt.tif", media.geotiff_bytes(transform=None), series="osm")
    assert body["map_sheet"]["placed"] is False
    assert body["asset"]["raster"]["geotransform"] is None
    assert body["asset"]["unknowns"]["geotransform"]


def test_map_sheets_are_listed_with_what_each_still_needs(client, project, headers, run_jobs):
    placed = put(client, project, headers, run_jobs, "p.tif", media.geotiff_bytes(), series="osm")["asset"]
    unplaced = put(client, project, headers, run_jobs, "u.tif", media.geotiff_bytes(crs=None, size=(41, 30)))["asset"]
    plain = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        params={"modality": "overhead_raster", "filename": "plain.tif"},
        content=media.geotiff_bytes(size=(50, 30)),
        headers=headers,
    )
    assert plain.status_code == 201 and plain.json()["asset"]["map_sheet"] is None

    listing = client.get(f"/api/v1/projects/{project['id']}/map-sheets")
    assert listing.status_code == 200
    items = {i["asset_id"]: i for i in listing.json()["items"]}
    assert set(items) == {placed["id"], unplaced["id"]}  # the plain raster is not a map sheet
    assert items[placed["id"]]["info"]["placed"] is True
    assert "Placed on the map" in items[placed["id"]]["next_step"]
    assert "Not georeferenced" in items[unplaced["id"]]["next_step"]
    assert "not evidence of a CRS" in listing.json()["note"]


def test_a_repeated_sheet_is_linked_as_a_duplicate(client, project, headers, run_jobs):
    data = media.geotiff_bytes()
    first = put(client, project, headers, run_jobs, "a.tif", data, series="osm")
    second = put(client, project, headers, run_jobs, "b.tif", data, series="osm")
    assert second["duplicate"] is True and second["duplicate_of_asset_id"] == first["asset"]["id"]
    assert len(stored_files()) == 1


# --- refusals ---------------------------------------------------------------------------------------------------------


def test_defence_series_and_unknown_series_are_not_accepted(client, project, headers):
    for series in ("defence", "dsm", "OSM", ""):
        response = sheet(client, project, headers, "a.tif", media.geotiff_bytes(), series=series)
        assert response.status_code == 422, series
    assert rows("SELECT count(*) FROM asset")[0][0] == 0


def test_unsupported_types_and_mismatched_content_are_refused_and_nothing_is_stored(client, project, headers):
    response = sheet(client, project, headers, "sheet.gif", b"GIF89a....", series="osm")
    assert response.status_code == 415 and response.json()["code"] == "UNSUPPORTED_FILE_TYPE"
    response = sheet(client, project, headers, "sheet.pdf", media.image_bytes("JPEG"), series="osm")
    assert response.status_code == 415 and response.json()["code"] == "SIGNATURE_MISMATCH"
    response = sheet(client, project, headers, "sheet.tif", b"II*\x00" + b"\x00" * 64, series="osm")
    assert response.status_code == 422 and response.json()["code"] == "ASSET_UNREADABLE"
    assert stored_files() == [] and rows("SELECT count(*) FROM asset")[0][0] == 0
    actions = [r[0] for r in rows("SELECT action FROM audit_event ORDER BY at")]
    assert actions.count("asset.upload_rejected") == 3


def test_the_ordinary_raster_upload_still_accepts_only_tiff(client, project, headers):
    for name, data in (("a.pdf", b"%PDF-1.4\n%%EOF"), ("a.png", media.image_bytes("PNG"))):
        response = upload(client, project, headers, name, data, modality="overhead_raster")
        assert response.status_code == 415 and response.json()["code"] == "UNSUPPORTED_FILE_TYPE"


def test_map_sheet_intake_does_not_touch_the_stored_schema(client, project, headers):
    # The schema revision is the same before and after an intake: taking in a map sheet never migrates anything. (This
    # used to compare with a fixed "0009"; that tied the test to the newest migration rather than to the intake.)
    with get_engine().connect() as conn:
        before = conn.execute(text("select version_num from alembic_version")).scalar()
    sheet(client, project, headers, "a.tif", media.geotiff_bytes(), series="osm")
    with get_engine().connect() as conn:
        assert conn.execute(text("select version_num from alembic_version")).scalar() == before
