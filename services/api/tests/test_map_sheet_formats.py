"""Map-sheet intake beyond the georeferenced GeoTIFF: scanned pictures, TIFF with world files, invalid sidecars, PDF.

All files are synthetic and made in memory (the PDFs are hand-written minimal files). A passing test here shows how the
application treats such inputs; it does not show that real Open Series or Everest Series sheets will read correctly.
"""

import hashlib

import pytest

from tests import media
from tests.test_assets import rows, stored_files
from tests.test_georeferencing import TO_LONLAT, alpha_at
from tests.test_map_sheets import layers, put, sheet
from tests.test_sidecars import GT, PRJ_UTM43, WORLD, added, apply, check, review

PAGE = b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] >> endobj\n"


def pdf_bytes(*, geo: bool = False, truncated: bool = False, encrypted: bool = False, pages: int = 1) -> bytes:
    """A minimal PDF written by hand: a catalog, a page tree and one page, optionally with GeoPDF-style entries."""
    viewport = (
        b"4 0 obj << /Type /Viewport /BBox [0 0 842 595] /Measure << /Type /Measure /Subtype /GEO "
        b"/GPTS [28.0 77.0 28.0 77.5 28.5 77.5 28.5 77.0] /LPTS [0 0 1 0 1 1 0 1] "
        b"/GCS << /Type /PROJCS /EPSG 32643 >> >> >> endobj\n"
        if geo
        else b""
    )
    body = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        + f"2 0 obj << /Type /Pages /Kids [3 0 R] /Count {pages} >> endobj\n".encode()
        + PAGE
        + viewport
        + (b"5 0 obj << /Filter /Standard >> endobj\n" if encrypted else b"")
        + b"trailer << /Root 1 0 R"
        + (b" /Encrypt 5 0 R" if encrypted else b"")
        + b" >>\nstartxref\n0\n"
    )
    return body if truncated else body + b"%%EOF\n"


def png(size=(400, 300), seed=1) -> bytes:
    return media.image_bytes("PNG", size=size, seed=seed)


# --- scanned pictures -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "fmt", "driver", "media_type"),
    [("scan.png", "PNG", "PNG", "image/png"), ("scan.jpg", "JPEG", "JPEG", "image/jpeg")],
)
def test_a_scanned_picture_is_stored_unmodified_and_left_unplaced(
    client, project, headers, run_jobs, name, fmt, driver, media_type
):
    data = media.image_bytes(fmt, size=(400, 300))
    body = put(client, project, headers, run_jobs, name, data, series="osm", sheet_label="Scan 7")
    asset, info = body["asset"], body["map_sheet"]

    assert asset["detected_format"] == driver and asset["media_type"] == media_type
    assert asset["modality"] == "overhead_raster" and asset["raster"]["driver"] == driver
    assert (asset["raster"]["width"], asset["raster"]["height"]) == (400, 300)
    assert asset["sha256"] == hashlib.sha256(data).hexdigest()
    assert [p.read_bytes() for p in stored_files()] == [data]

    assert info["intake"] == "scanned_image" and info["placed"] is False and info["georeferencing_basis"] == "none"
    assert {"MAP_SHEET", "MAP_SHEET_SCANNED", "MISSING_CRS", "NOT_GEOREFERENCED"} <= set(asset["quality_flags"])
    assert asset["raster"]["crs_status"] == "missing" and asset["raster"]["geotransform"] is None
    assert asset["raster"]["footprint_4326"] is None

    # A picture is viewable (a preview is made) but is never put on the map.
    preview = client.get(f"/api/v1/assets/{asset['id']}/preview")
    assert preview.status_code == 200
    body = layers(client, project)
    assert body["rasters"] == []
    (entry,) = body["excluded"]
    assert entry["reason"] == "CRS_MISSING" and entry["georeferencing_can_be_recorded"] is True
    assert entry["map_sheet"]["intake"] == "scanned_image"


def test_a_scanned_tiff_without_georeferencing_is_a_scanned_raster(client, project, headers, run_jobs):
    data = media.geotiff_bytes(crs=None, transform=None, size=(120, 90), bands=3)
    body = put(client, project, headers, run_jobs, "scan.tif", data, series="esm")
    assert body["map_sheet"]["intake"] == "scanned_raster" and body["map_sheet"]["placed"] is False
    assert "MAP_SHEET_SCANNED" in body["asset"]["quality_flags"]


def test_a_corrupt_picture_is_refused_and_nothing_is_stored(client, project, headers):
    broken = media.image_bytes("PNG")[:40]
    response = sheet(client, project, headers, "broken.png", broken, series="osm")
    assert response.status_code == 422 and response.json()["code"] == "ASSET_UNREADABLE"
    assert stored_files() == [] and rows("SELECT count(*) FROM asset")[0][0] == 0


# --- TIFF with world file and .prj ------------------------------------------------------------------------------------


def test_a_tiff_with_a_world_file_and_prj_is_placed_only_after_a_person_confirms(client, project, headers, run_jobs):
    data = media.geotiff_bytes(crs=None, transform=None, size=(200, 150))
    body = put(client, project, headers, run_jobs, "scan.tif", data, series="osm", sheet_label="NF 43-02")
    asset = body["asset"]
    added(client, project, headers, "scan.tfw", WORLD)
    added(client, project, headers, "scan.prj", PRJ_UTM43)

    # Uploading the sidecars places nothing.
    assert client.get(f"/api/v1/assets/{asset['id']}").json()["map_sheet"]["placed"] is False
    assert [e["reason"] for e in layers(client, project)["excluded"]] == ["CRS_MISSING"]

    reviewed = review(client, asset)
    assert reviewed["proposal"]["crs_id"] == "EPSG:32643" and reviewed["proposal"]["geotransform"] == GT
    assert check(client, asset, reason="Supplier delivery note 114")["validation"]["valid"] is True
    done = apply(client, headers, asset, reviewed)
    assert done.status_code == 201, done.text

    info = client.get(f"/api/v1/assets/{asset['id']}").json()["map_sheet"]
    assert info["placed"] is True and info["georeferencing_basis"] == "sidecar"
    assert info["declaration"]["series"] == "osm"  # unchanged by georeferencing
    (layer,) = layers(client, project)["rasters"]
    assert layer["placement"] == "user_asserted" and layer["map_sheet"]["georeferencing_basis"] == "sidecar"
    lon, lat = TO_LONLAT.transform(501000, 2999000)
    assert alpha_at(client, asset, lon, lat, z=15, version=1) > 0

    # The original is untouched and the record says what was used.
    assert [p.read_bytes() for p in stored_files() if p.name == asset["sha256"]] == [data]
    assessment = client.get(f"/api/v1/assets/{asset['id']}/map-sheet").json()
    assert {s["filename"] for s in assessment["sidecars"]} == {"scan.tfw", "scan.prj"}


def test_invalid_sidecars_are_kept_but_never_applied(client, project, headers, run_jobs):
    data = media.geotiff_bytes(crs=None, transform=None, size=(200, 150))
    asset = put(client, project, headers, run_jobs, "scan.tif", data, series="osm")["asset"]
    world = added(client, project, headers, "scan.tfw", "10\nabc\n0\n-10\n500005\n")  # too short, not a number
    prj = added(client, project, headers, "scan.prj", "this is not a coordinate system")
    assert world["status"] != "parsed" and prj["status"] != "parsed"

    reviewed = review(client, asset)
    assert reviewed["state"] != "proposal"
    assert client.get(f"/api/v1/assets/{asset['id']}/georeferencing").json()["current"]["version"] == 0
    assert layers(client, project)["rasters"] == []
    assert client.get(f"/api/v1/assets/{asset['id']}").json()["map_sheet"]["placed"] is False


# --- PDF --------------------------------------------------------------------------------------------------------------


def test_a_pdf_is_stored_scanned_for_facts_and_never_placed(client, project, headers, run_jobs):
    data = pdf_bytes()
    body = put(client, project, headers, run_jobs, "NF43-03.pdf", data, series="osm", sheet_label="NF 43-03")
    asset, info = body["asset"], body["map_sheet"]

    assert asset["detected_format"] == "PDF" and asset["media_type"] == "application/pdf"
    assert asset["modality"] == "overhead_raster" and asset["raster"] is None and asset["georeferencing"] is None
    assert asset["sha256"] == hashlib.sha256(data).hexdigest()
    assert [p.read_bytes() for p in stored_files()] == [data]
    assert info["intake"] == "pdf_document" and info["placed"] is False and info["georeferencing_basis"] == "none"
    assert {"MAP_SHEET", "MAP_SHEET_PDF", "NOT_GEOREFERENCED", "PDF_NOT_RENDERED"} <= set(asset["quality_flags"])
    assert asset["status"] == "validated"  # a PDF being a PDF is a fact, not a warning
    assert "renderer" in asset["unknowns"]["pdf_rendering"]

    pdf = asset["metadata"]["pdf"]
    assert pdf["pdf_version"] == "1.4" and pdf["page_count"] == 1 and pdf["encrypted"] is False
    assert pdf["first_page_size"]["width_pt"] == 842 and pdf["first_page_size"]["width_mm"] == pytest.approx(
        297.0, abs=0.1
    )
    assert pdf["geo_markers"] == [] and pdf["declared_corner_points"] is None

    # No picture job was queued for it, and it never reaches the map.
    assert asset["preview"]["status"] == "none"
    assert rows("SELECT count(*) FROM job")[0][0] == 0
    body = layers(client, project)
    assert body["rasters"] == []
    (entry,) = body["excluded"]
    assert entry["reason"] == "NOT_GEOREFERENCED" and entry["map_sheet"]["intake"] == "pdf_document"
    assert "TIFF or PNG" in entry["detail"]

    # The raster tools do not pretend a PDF is a raster.
    assert client.get(f"/api/v1/assets/{asset['id']}/georeferencing").status_code == 409
    assert client.get(f"/api/v1/assets/{asset['id']}/crs-assessment").status_code == 409
    sheet_view = client.get(f"/api/v1/assets/{asset['id']}/map-sheet")
    assert sheet_view.status_code == 200 and sheet_view.json()["crs_assessment"] is None


def test_geopdf_markers_are_reported_as_unverified_and_nothing_is_placed(client, project, headers, run_jobs):
    body = put(client, project, headers, run_jobs, "geo.pdf", pdf_bytes(geo=True), series="osm")
    asset = body["asset"]
    pdf = asset["metadata"]["pdf"]
    assert "ISO 32000 geospatial measure (/GEO)" in pdf["geo_markers"]
    assert pdf["declared_epsg"] == 32643 and len(pdf["declared_corner_points"]) == 4
    assert pdf["declared_corner_points"][0] == {"latitude": 28.0, "longitude": 77.0}
    assert "PDF_GEO_METADATA_UNVERIFIED" in asset["quality_flags"] and asset["status"] == "validated_with_warnings"
    assert "not applied" in asset["unknowns"]["pdf_geo_metadata"]
    assert body["map_sheet"]["placed"] is False and layers(client, project)["rasters"] == []
    assert rows("SELECT count(*) FROM georef_assertion")[0][0] == 0


def test_an_encrypted_or_truncated_pdf_is_handled_honestly(client, project, headers, run_jobs):
    encrypted = put(client, project, headers, run_jobs, "locked.pdf", pdf_bytes(encrypted=True), series="osm")["asset"]
    assert "PDF_ENCRYPTED" in encrypted["quality_flags"] and encrypted["metadata"]["pdf"]["encrypted"] is True
    cut = sheet(client, project, headers, "cut.pdf", pdf_bytes(truncated=True), series="osm")
    assert cut.status_code == 422 and cut.json()["code"] == "ASSET_UNREADABLE"
    assert len(stored_files()) == 1


def test_a_pdf_cannot_be_given_control_points(client, project, headers, run_jobs):
    asset = put(client, project, headers, run_jobs, "a.pdf", pdf_bytes(), series="osm")["asset"]
    response = client.post(
        f"/api/v1/assets/{asset['id']}/control-points/validate",
        json={
            "crs_id": "EPSG:32643",
            "points": [{"column": c, "row": r, "x": c, "y": r} for c, r in ((0, 0), (10, 0), (0, 10))],
            "source": "s",
            "reason": "r",
        },
    )
    assert response.status_code == 409 and response.json()["code"] == "NOT_A_RASTER"
