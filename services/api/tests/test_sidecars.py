"""Phase 3b follow-up: sidecar metadata (.tfw/.wld, .prj, .aux.xml) reviewed and confirmed by a person.

Every raster and sidecar here is synthetic, written in memory. Expected coordinates are worked out independently in
each test from the world-file convention (the file gives the centre of the top-left pixel) and from the affine
formula x = x0 + col*a + row*b, y = y0 + col*d + row*e, then transformed with PyProj. Synthetic validation only; no
real-world imagery is involved.
"""

import hashlib
import math
import os
import stat
from types import SimpleNamespace

import pyproj
import pytest
from pyproj.enums import WktVersion
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from geopoc.config import get_settings
from geopoc.db.session import get_engine
from geopoc.geo import sidecars
from geopoc.storage import store
from tests.test_georeferencing import TO_LONLAT, alpha_at, layers, raster, record, state

WORLD = "10\n0\n0\n-10\n500005\n2999995\n"  # centre of the top-left pixel -> corner (500000, 3000000)
GT = [500000.0, 10.0, 0.0, 3000000.0, 0.0, -10.0]
PRJ_UTM43 = pyproj.CRS("EPSG:32643").to_wkt(WktVersion.WKT1_ESRI)
PRJ_UTM44 = pyproj.CRS("EPSG:32644").to_wkt(WktVersion.WKT1_ESRI)
REASON = "World file and .prj delivered with the scan by the supplier (delivery note 2026-114)."
UNPLACED = {"crs": None, "transform": None, "size": (200, 150)}


def put(client, project, headers, filename, data, asset_id=None):
    params = {"filename": filename, **({"asset_id": asset_id} if asset_id else {})}
    body = data.encode() if isinstance(data, str) else data
    return client.post(f"/api/v1/projects/{project['id']}/sidecars", params=params, content=body, headers=headers)


def added(client, project, headers, filename, data, asset_id=None):
    response = put(client, project, headers, filename, data, asset_id)
    assert response.status_code == 201, response.text
    return response.json()["sidecar"]


def review(client, asset):
    response = client.get(f"/api/v1/assets/{asset['id']}/sidecars")
    assert response.status_code == 200, response.text
    return response.json()


def check(client, asset, **body):
    response = client.post(f"/api/v1/assets/{asset['id']}/sidecars/validate", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, headers, asset, reviewed, confirm=True, **body):
    payload = {
        "expected_version": reviewed["expected_version"],
        "sidecar_ids": reviewed["sidecar_ids"],
        "confirm": confirm,
        "reason": REASON,
        **body,
    }
    return client.post(f"/api/v1/assets/{asset['id']}/sidecars/apply", json=payload, headers=headers)


def codes(validation) -> dict[str, str]:
    return {c["code"]: c["status"] for c in validation["checks"]}


def aux(*children: str, root="PAMDataset") -> str:
    return f"<{root}>{''.join(children)}</{root}>"


def stored_bytes(sha256: str) -> bytes:
    return store.absolute_path(store.relative_path(sha256)).read_bytes()


def audit(prefix: str):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT action, details FROM audit_event WHERE action LIKE :p ORDER BY at"), {"p": f"{prefix}%"}
        )
        return rows.all()


# --- the complete workflow --------------------------------------------------------------------------------------------


def test_a_world_file_and_prj_are_reviewed_confirmed_and_recorded(client, project, headers, run_jobs, profile):
    asset, body = raster(client, project, headers, run_jobs, **UNPLACED)
    world = added(client, project, headers, "scan.tfw", WORLD)
    prj = added(client, project, headers, "scan.prj", PRJ_UTM43)
    assert world["pairing"] == prj["pairing"] == "filename_match"
    assert world["status"] == prj["status"] == "parsed"
    assert world["values"]["lines"] == {"A": 10, "D": 0, "B": 0, "E": -10, "C": 500005, "F": 2999995}
    assert world["values"]["geotransform"] == GT
    assert prj["values"]["crs"]["identifier"] == "EPSG:32643"
    assert "No CRS" in world["summary"]  # a world file never claims a CRS
    # Stored exactly as received.
    assert (
        stored_bytes(world["sha256"]) == WORLD.encode()
        and world["sha256"] == hashlib.sha256(WORLD.encode()).hexdigest()
    )

    # Nothing is applied by uploading: the raster is still unplaced, at version 0.
    assert state(client, asset)["current"]["version"] == 0
    assert [e["reason"] for e in layers(client, project)["excluded"]] == ["CRS_MISSING"]

    reviewed = review(client, asset)
    assert reviewed["state"] == "proposal" and reviewed["expected_version"] == 0
    proposal = reviewed["proposal"]
    assert proposal["operation"] == "set_geotransform" and proposal["crs_id"] == "EPSG:32643"
    assert proposal["geotransform"] == GT and proposal["world_file"] == WORLD
    assert proposal["sources"]["geotransform"]["filename"] == "scan.tfw"
    assert proposal["sources"]["crs"]["filename"] == "scan.prj"
    assert "scan.tfw" in proposal["geotransform_source"] and world["sha256"][:16] in proposal["geotransform_source"]

    # A reason is mandatory; validating stores nothing.
    without_reason = check(client, asset)
    assert without_reason["validation"]["valid"] is False
    assert codes(without_reason["validation"])["REASON_RECORDED"] == "failed"
    validated = check(client, asset, reason=REASON)["validation"]
    assert validated["valid"] is True, validated["checks"]
    found = codes(validated)
    assert found["SIDECAR_UNCHANGED"] == "ok" and found["COORDINATES_FIT_CRS"] == "ok" and found["TILE_RENDERS"] == "ok"
    corners = {c["point"]: c for c in validated["corners"]}
    assert corners["top-left corner"]["native"] == [500000.0, 3000000.0]
    assert corners["bottom-right corner"]["native"] == [502000.0, 2998500.0]  # 200 x 10 m, 150 x 10 m
    assert corners["top-left corner"]["lon_lat"] == pytest.approx(list(TO_LONLAT.transform(500000, 3000000)), abs=1e-9)
    assert state(client, asset)["current"]["version"] == 0

    unconfirmed = apply(client, headers, asset, reviewed, confirm=False)
    assert unconfirmed.status_code == 422 and unconfirmed.json()["code"] == "CONFIRMATION_REQUIRED"
    blank = apply(client, headers, asset, reviewed, reason="   ")
    assert blank.status_code == 422 and blank.json()["code"] == "GEOREFERENCE_INVALID"
    assert [e["field"] for e in blank.json()["errors"]] == ["REASON_RECORDED"]

    done = apply(client, headers, asset, reviewed)
    assert done.status_code == 201, done.text
    after = done.json()
    assert after["current"]["georeferenced"] is True and after["current"]["source"] == "recorded"
    assert after["current"]["crs_id"] == "EPSG:32643" and after["current"]["geotransform"] == GT
    (version,) = after["history"]
    assert version["operation"] == "set_geotransform" and version["method"] == "sidecar_geotransform"
    assert version["world_file"] == WORLD and "scan.tfw" in version["geotransform_source"]
    assert version["reason"] == REASON and version["actor_label"] == profile["display_name"]
    assert version["source_checksum"] == hashlib.sha256(body).hexdigest()
    used = {s["filename"]: s for s in version["validation"]["sidecars"]}
    assert used["scan.tfw"]["sha256"] == world["sha256"] and used["scan.tfw"]["role"] == ["geotransform"]
    assert used["scan.prj"]["sha256"] == prj["sha256"] and used["scan.prj"]["role"] == ["crs"]
    assert {"SIDECAR_UNCHANGED", "ORIGINAL_UNCHANGED", "TILE_RENDERS"} <= {
        c["code"] for c in version["validation"]["checks"]
    }

    # Placed, labelled as user-asserted, drawn from the unchanged original.
    (layer,) = layers(client, project)["rasters"]
    assert layer["placement"] == "user_asserted" and layer["tile_url"].endswith("?v=1")
    assert alpha_at(client, asset, *TO_LONLAT.transform(501000, 2999250), version=1) == 255
    assert client.get(f"/api/v1/assets/{asset['id']}/content").content == body
    assert stored_bytes(world["sha256"]) == WORLD.encode() and stored_bytes(prj["sha256"]) == PRJ_UTM43.encode()

    # The review now says the values are in use; the file's own metadata is still the file's.
    assert review(client, asset)["state"] == "in_use"
    assert client.get(f"/api/v1/assets/{asset['id']}").json()["raster"]["crs_status"] == "missing"
    actions = [a for a, _ in audit("sidecar.")] + [a for a, _ in audit("georeference.")]
    assert actions == ["sidecar.upload", "sidecar.upload", "georeference.refused", "georeference.set_geotransform"]
    (_, details) = audit("georeference.set_geotransform")[0]
    assert details["method"] == "sidecar_geotransform" and set(details["sidecars"]) == {world["id"], prj["id"]}


def test_a_world_file_alone_does_not_establish_a_crs(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    added(client, project, headers, "scan.tfw", WORLD)
    reviewed = review(client, asset)
    assert reviewed["state"] == "insufficient" and reviewed["proposal"] is None
    text_ = " ".join(reviewed["explanation"])
    assert "not which coordinate system" in text_ and "not placed" in text_
    refused = apply(client, headers, asset, reviewed)
    assert refused.status_code == 422 and refused.json()["code"] == "SIDECAR_NOTHING_TO_RECORD"
    current = state(client, asset)["current"]
    assert current["version"] == 0 and current["georeferenced"] is False


def test_a_world_file_keeps_a_crs_recorded_earlier(client, project, headers, run_jobs):
    """A raster with a CRS but no geotransform: the world file supplies the coordinates, the CRS in use is kept."""
    asset, _ = raster(client, project, headers, run_jobs, crs="EPSG:32643", transform=None, size=(200, 150))
    added(client, project, headers, "scan.tfw", WORLD)
    reviewed = review(client, asset)
    assert reviewed["state"] == "proposal"
    assert reviewed["proposal"]["crs_id"] is None and reviewed["proposal"]["sources"]["crs"]["from"] == "file"
    assert apply(client, headers, asset, reviewed).status_code == 201


def test_a_prj_alone_cannot_place_a_raster_without_coordinates(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    added(client, project, headers, "scan.prj", PRJ_UTM43)
    reviewed = review(client, asset)
    assert reviewed["state"] == "insufficient"
    assert any(".prj gives only a CRS" in line for line in reviewed["explanation"])


def test_a_prj_assigns_a_crs_to_coordinates_the_file_already_has(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, crs=None, size=(200, 150))  # geotransform, no CRS
    added(client, project, headers, "scan.tfw", WORLD)  # agrees with the file's own geotransform
    added(client, project, headers, "scan.prj", PRJ_UTM43)
    reviewed = review(client, asset)
    assert reviewed["state"] == "proposal" and reviewed["conflicts"] == []
    assert reviewed["proposal"]["operation"] == "assign_crs" and reviewed["proposal"]["crs_id"] == "EPSG:32643"
    assert reviewed["proposal"]["sources"]["geotransform"] == {"from": "file", "filename": None}
    world = next(s for s in reviewed["sidecars"] if s["kind"] == "world_file")
    (comparison,) = [c for c in world["comparisons"] if c["field"] == "geotransform"]
    assert comparison["relation_file"] == "same"
    done = apply(client, headers, asset, reviewed)
    assert done.status_code == 201, done.text
    assert done.json()["history"][0]["method"] == "sidecar_crs"
    assert done.json()["history"][0]["geotransform_source"] is None  # the coordinates are the file's own


def test_valid_raster_metadata_is_never_overridden_and_conflicts_are_shown(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, size=(200, 150))  # valid CRS and geotransform
    added(client, project, headers, "scan.tfw", "10\n0\n0\n-10\n600005\n2999995\n")
    added(client, project, headers, "scan.prj", PRJ_UTM44)
    reviewed = review(client, asset)
    assert reviewed["state"] == "file_valid" and reviewed["proposal"] is None
    assert len(reviewed["conflicts"]) == 2
    assert all("Shown for comparison only" in s["role"] for s in reviewed["sidecars"])
    refused = apply(client, headers, asset, reviewed)
    assert refused.status_code == 409 and refused.json()["code"] == "ALREADY_GEOREFERENCED"
    current = state(client, asset)["current"]
    assert current["version"] == 0 and current["source"] == "file" and current["crs_id"] == "EPSG:32643"


def test_a_world_file_that_disagrees_with_the_file_is_shown_and_the_file_value_kept(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, crs=None, size=(200, 150))
    added(client, project, headers, "scan.tfw", "10\n0\n0\n-10\n600005\n2999995\n")
    added(client, project, headers, "scan.prj", PRJ_UTM43)
    reviewed = review(client, asset)
    assert reviewed["state"] == "proposal" and reviewed["proposal"]["operation"] == "assign_crs"
    (conflict,) = reviewed["conflicts"]
    assert "scan.tfw" in conflict and "geotransform" in conflict
    validated = check(client, asset, reason=REASON)["validation"]
    assert codes(validated)["SIDECAR_CONFLICT"] == "warning"
    done = apply(client, headers, asset, reviewed).json()
    assert done["current"]["geotransform"] == GT  # the file's geotransform, not the world file's


def test_contradictory_sidecars_need_a_choice(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    world = added(client, project, headers, "scan.tfw", WORLD)
    other = added(
        client,
        project,
        headers,
        "scan.tif.aux.xml",
        aux(f"<SRS>{PRJ_UTM44}</SRS>", "<GeoTransform> 6.0e5, 10, 0, 3.0e6, 0, -10</GeoTransform>"),
    )
    prj = added(client, project, headers, "scan.prj", PRJ_UTM43)
    assert other["values"]["geotransform"] == [600000.0, 10.0, 0.0, 3000000.0, 0.0, -10.0]
    reviewed = review(client, asset)
    assert reviewed["state"] == "conflict" and reviewed["proposal"] is None
    assert {c["filename"] for c in reviewed["choices"]["geotransform"]} == {"scan.tfw", "scan.tif.aux.xml"}
    assert {c["filename"] for c in reviewed["choices"]["crs"]} == {"scan.prj", "scan.tif.aux.xml"}
    refused = apply(client, headers, asset, reviewed)
    assert refused.status_code == 409 and refused.json()["code"] == "SIDECAR_NOTHING_TO_RECORD"

    chosen = check(client, asset, reason=REASON, geotransform_from=world["id"], crs_from=prj["id"])
    assert chosen["review"]["state"] == "proposal" and chosen["validation"]["valid"] is True
    bad = client.post(
        f"/api/v1/assets/{asset['id']}/sidecars/validate", json={"geotransform_from": prj["id"], "reason": REASON}
    )
    assert bad.status_code == 422 and bad.json()["code"] == "SIDECAR_CHOICE_INVALID"
    done = apply(client, headers, asset, reviewed, geotransform_from=other["id"], crs_from=other["id"])
    assert done.status_code == 201, done.text
    version = done.json()["history"][0]
    assert version["geotransform"] == [600000.0, 10.0, 0.0, 3000000.0, 0.0, -10.0] and version["crs_id"] == "EPSG:32644"
    assert [s["filename"] for s in version["validation"]["sidecars"]] == ["scan.tif.aux.xml"]
    assert version["validation"]["sidecars"][0]["role"] == ["geotransform", "crs"]


def test_a_later_sidecar_supersedes_an_earlier_one_of_the_same_kind(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    first = added(client, project, headers, "scan.tfw", "10\n0\n0\n-10\n600005\n2999995\n")
    second = added(client, project, headers, "scan.wld", WORLD)
    added(client, project, headers, "scan.prj", PRJ_UTM43)
    reviewed = review(client, asset)
    by_id = {s["id"]: s for s in reviewed["sidecars"]}
    assert by_id[first["id"]]["current"] is False and "Superseded by scan.wld" in by_id[first["id"]]["role"]
    assert by_id[second["id"]]["current"] is True
    assert reviewed["state"] == "proposal" and reviewed["proposal"]["geotransform"] == GT


# --- malformed, unsupported and unresolved content ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "content", "status", "problem"),
    [
        ("scan.tfw", "10\n0\n0\n-10\n500005\n", "malformed", "exactly six numbers"),
        ("scan.tfw", "10,0\n0\n0\n-10\n500005\n2999995\n", "malformed", "Not a plain decimal number"),
        ("scan.tfw", "nan\n0\n0\n-10\n500005\n2999995\n", "malformed", "Not a plain decimal number"),
        ("scan.tfw", "0\n0\n0\n0\n500005\n2999995\n", "parsed", "degenerate"),
        ("scan.tfw", b"\xff\xfe1\x000\x00", "malformed", "NUL bytes"),
        ("scan.prj", "this is not wkt", "malformed", "Not a CRS definition PROJ can read"),
        ("scan.prj", "+proj=utm +zone=43 +datum=WGS84", "unsupported", "PROJ strings"),
        ("scan.prj", "   ", "malformed", "no CRS definition"),
        ("scan.aux.xml", "<PAMDataset><SRS>", "malformed", "XML error"),
        ("scan.aux.xml", "<Other/>", "unsupported", "not <PAMDataset>"),
        (
            "scan.aux.xml",
            '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///C:/Windows/win.ini">]><PAMDataset>&x;'
            "</PAMDataset>",
            "malformed",
            "entity declaration",
        ),
        ("scan.aux.xml", aux("<GeoTransform>1, 2, 3</GeoTransform>"), "unsupported", "six plain numbers"),
    ],
)
def test_malformed_and_unsupported_sidecars_are_kept_and_reported(
    client, project, headers, run_jobs, filename, content, status, problem
):
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    sidecar = added(client, project, headers, filename, content)
    assert sidecar["status"] == status
    assert any(problem in p for p in sidecar["problems"]), sidecar["problems"]
    reviewed = review(client, asset)
    assert reviewed["state"] == "insufficient" and reviewed["proposal"] is None
    assert any(problem in line for line in reviewed["explanation"])
    assert state(client, asset)["current"]["georeferenced"] is False


CUSTOM_LCC = (
    'PROJCS["Custom_LCC",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],PROJECTION["Lambert_Conformal_Conic"],'
    'PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",80.0],'
    'PARAMETER["Standard_Parallel_1",20.0],PARAMETER["Standard_Parallel_2",30.0],'
    'PARAMETER["Latitude_Of_Origin",25.0],UNIT["Meter",1.0]]'
)
WRONG_MERIDIAN = (
    pyproj.CRS("EPSG:32643")
    .to_wkt(WktVersion.WKT1_GDAL)
    .replace('PARAMETER["central_meridian",75]', 'PARAMETER["central_meridian",76]')
)


@pytest.mark.parametrize(
    ("wkt", "problem"),
    [
        (CUSTOM_LCC, "No CRS in the local registry matches this definition completely"),
        (WRONG_MERIDIAN, "No CRS in the local registry matches this definition completely"),
        (pyproj.CRS("EPSG:5703").to_wkt(WktVersion.WKT1_GDAL), "vertical CRS"),
        (pyproj.CRS("EPSG:4978").to_wkt(WktVersion.WKT1_GDAL), "geocentric CRS"),
        (pyproj.CRS("EPSG:7405").to_wkt(WktVersion.WKT1_GDAL), "compound CRS"),
    ],
)
def test_crs_definitions_the_registry_cannot_resolve_are_not_used(client, project, headers, run_jobs, wkt, problem):
    assert "UTM zone 43N" in WRONG_MERIDIAN  # same name as EPSG:32643, different parameters
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    added(client, project, headers, "scan.tfw", WORLD)
    prj = added(client, project, headers, "scan.prj", wkt)
    assert prj["status"] == "parsed" and any(problem in p for p in prj["problems"]), prj["problems"]
    assert "it is not used" in prj["summary"]
    reviewed = review(client, asset)
    assert reviewed["state"] == "insufficient" and reviewed["proposal"] is None
    listed = next(s for s in reviewed["sidecars"] if s["id"] == prj["id"])
    (comparison,) = [c for c in listed["comparisons"] if c["field"] == "CRS"]
    assert comparison["relation_file"] == comparison["relation_in_use"] == "unresolved"
    assert state(client, asset)["current"]["georeferenced"] is False


@pytest.mark.parametrize("code", ["EPSG:32643", "EPSG:4326", "EPSG:27700", "EPSG:3857"])
@pytest.mark.parametrize("version", [WktVersion.WKT1_ESRI, WktVersion.WKT1_GDAL, WktVersion.WKT2_2019])
def test_prj_dialects_resolve_to_the_registry_code(code, version):
    parsed = sidecars.parse_prj(pyproj.CRS(code).to_wkt(version).encode())
    assert parsed.status == "parsed" and parsed.crs_id == code, parsed.problems


def test_a_rotated_world_file_is_used_in_full(client, project, headers, run_jobs):
    a, b, d, e = 10 * math.cos(math.radians(30)), 10 * math.sin(math.radians(30)), 10 * math.sin(math.radians(30)), -10 * math.cos(math.radians(30))  # fmt: skip
    centre_x, centre_y = 500000 + a / 2 + b / 2, 3000000 + d / 2 + e / 2
    world = "\n".join(repr(v) for v in (a, d, b, e, centre_x, centre_y))
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    sidecar = added(client, project, headers, "scan.tfw", world)
    assert sidecar["values"]["rotated"] is True
    added(client, project, headers, "scan.prj", PRJ_UTM43)
    validated = check(client, asset, reason=REASON)["validation"]
    assert validated["valid"] is True, validated["checks"]
    assert codes(validated)["GEOTRANSFORM_VALID"] == "warning"  # rotated: said, not hidden
    corners = {c["point"]: c["native"] for c in validated["corners"]}
    assert corners["top-left corner"] == pytest.approx([500000, 3000000], abs=1e-6)
    expected = [500000 + 200 * a + 150 * b, 3000000 + 200 * d + 150 * e]  # x0 + col*a + row*b, y0 + col*d + row*e
    assert corners["bottom-right corner"] == pytest.approx(expected, abs=1e-6)
    done = apply(client, headers, asset, review(client, asset))
    assert done.status_code == 201
    assert done.json()["current"]["geotransform"] == pytest.approx([500000, a, b, 3000000, d, e], abs=1e-6)


# --- .aux.xml ------------------------------------------------------------------------------------------------------------


def test_aux_xml_band_metadata_is_shown_not_applied_and_unknown_content_is_not_imported(
    client, project, headers, run_jobs
):
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    before = client.get(f"/api/v1/assets/{asset['id']}/raster-tools").json()["bands"]
    content = aux(
        f'<SRS dataAxisToSRSAxisMapping="1,2">{PRJ_UTM43}</SRS>',
        "<GeoTransform>500000, 10, 0, 3000000, 0, -10</GeoTransform>",
        '<Metadata domain="RPC"><MDI key="LINE_OFF">1</MDI></Metadata>',
        "<Metadata><MDI key='AREA_OR_POINT'>Area</MDI></Metadata>",
        "<GCPList><GCP Id='1' Pixel='0' Line='0' X='1' Y='2'/></GCPList>",
        "<FancyThing/>",
        '<PAMRasterBand band="1"><Description>red, as supplied</Description><NoDataValue>0</NoDataValue>'
        "<Scale>0.5</Scale><Offset>1</Offset><UnitType>m</UnitType><Histograms/></PAMRasterBand>",
        '<PAMRasterBand band="5"><NoDataValue>0</NoDataValue></PAMRasterBand>',
        '<PAMRasterBand band="1"><NoDataValue>-9999</NoDataValue></PAMRasterBand>',
    )
    sidecar = added(client, project, headers, "scan.aux.xml", content)
    assert sidecar["status"] == "parsed" and sidecar["values"]["crs"]["identifier"] == "EPSG:32643"
    ignored = " ".join(sidecar["ignored"])
    for expected in ("RPC sensor model", "domain 'default'", "1 ground control point", "<FancyThing>", "<Histograms>"):
        assert expected in ignored
    assert any("band '5'" in p for p in sidecar["problems"])
    assert any("-9999 cannot occur in a uint8 band" in p for p in sidecar["problems"])
    band_rows = [c for c in sidecar["comparisons"] if c["field"].startswith("band 1")]
    assert {c["field"] for c in band_rows} >= {"band 1 description", "band 1 nodata", "band 1 scale", "band 1 units"}
    assert all(c["applied"] is False for c in band_rows)

    done = apply(client, headers, asset, review(client, asset))
    assert done.status_code == 201, done.text
    # Band metadata was not applied: what the raster tools read is unchanged.
    assert client.get(f"/api/v1/assets/{asset['id']}/raster-tools").json()["bands"] == before
    pixel = client.get(f"/api/v1/assets/{asset['id']}/pixel", params={"col": 0, "row": 0}).json()["values"][0]
    assert pixel["nodata"] is False and pixel["units"] is None and pixel["scaled"] in (None, pixel["raw"])


def test_aux_xml_axis_mapping_must_be_x_y():
    raster_meta = SimpleNamespace(band_count=1, dtypes=["uint8"])
    lonlat = pyproj.CRS("EPSG:4326").to_wkt()
    ok = sidecars.parse_aux_xml(aux(f'<SRS dataAxisToSRSAxisMapping="2,1">{lonlat}</SRS>').encode(), raster_meta)
    assert ok.crs_id == "EPSG:4326"
    swapped = sidecars.parse_aux_xml(aux(f'<SRS dataAxisToSRSAxisMapping="1,2">{lonlat}</SRS>').encode(), raster_meta)
    assert swapped.crs_id is None and any("axis mapping" in p for p in swapped.problems)


# --- pairing and upload checks ---------------------------------------------------------------------------------------------


def test_sidecars_pair_by_name_case_insensitively_or_are_attached_explicitly(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, name="Scene_01.TIF", **UNPLACED)
    assert added(client, project, headers, "scene_01.tfw", WORLD)["pairing"] == "filename_match"
    assert added(client, project, headers, "SCENE_01.tif.aux.xml", aux("<FancyThing/>"))["pairing"] == "filename_match"

    orphan = put(client, project, headers, "elsewhere.prj", PRJ_UTM43)
    assert orphan.status_code == 422 and orphan.json()["code"] == "SIDECAR_NO_MATCHING_RASTER"
    explicit = client.post(
        f"/api/v1/assets/{asset['id']}/sidecars",
        params={"filename": "elsewhere.prj"},
        content=PRJ_UTM43.encode(),
        headers=headers,
    )
    assert explicit.status_code == 201 and explicit.json()["sidecar"]["pairing"] == "explicit"
    assert "do not match" in explicit.json()["message"]

    again = put(client, project, headers, "scene_01.tfw", WORLD)
    assert again.status_code == 200 and again.json()["duplicate"] is True

    unsupported = put(client, project, headers, "scene_01.txt", "hello")
    assert unsupported.status_code == 415 and unsupported.json()["code"] == "SIDECAR_UNSUPPORTED_TYPE"
    empty = put(client, project, headers, "scene_01.prj", b"")
    assert empty.status_code == 422 and empty.json()["code"] == "EMPTY_FILE"
    too_large = put(client, project, headers, "scene_01.tfw", b"1\n" * 40000)
    assert too_large.status_code == 413 and too_large.json()["code"] == "FILE_TOO_LARGE"
    assert [a for a, _ in audit("sidecar.upload_rejected")] == ["sidecar.upload_rejected"] * 4


def test_ambiguous_pairing_is_refused(client, project, headers, run_jobs):
    raster(client, project, headers, run_jobs, **UNPLACED)
    raster(client, project, headers, run_jobs, crs=None, transform=None, size=(201, 150))  # another scan.tif
    response = put(client, project, headers, "scan.tfw", WORLD)
    assert response.status_code == 409 and response.json()["code"] == "SIDECAR_PAIRING_AMBIGUOUS"


def test_sidecars_belong_to_rasters_only(client, project, headers, run_jobs):
    from tests import media
    from tests.test_assets import upload

    photo = upload(client, project, headers, "scan.png", media.image_bytes("PNG")).json()["asset"]
    response = put(client, project, headers, "scan.prj", PRJ_UTM43, asset_id=photo["id"])
    assert response.status_code == 409 and response.json()["code"] == "NOT_A_RASTER"


def test_ingestion_rules_list_the_sidecar_extensions(client):
    rules = {r["modality"]: r for r in client.get("/api/v1/ingestion/rules").json()["rules"]}
    assert rules["overhead_raster"]["sidecar_extensions"] == [".tfw", ".tifw", ".tiffw", ".wld", ".prj", ".aux.xml"]
    assert rules["ground_image"]["sidecar_extensions"] == []


# --- concurrency, integrity, history and tiles -----------------------------------------------------------------------------


def test_concurrent_changes_and_new_sidecars_after_review_are_refused(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    added(client, project, headers, "scan.tfw", WORLD)
    added(client, project, headers, "scan.prj", PRJ_UTM43)
    reviewed = review(client, asset)

    # Someone else records georeferencing by hand in the meantime.
    other = record(
        client, headers, asset, operation="set_geotransform", crs_id="EPSG:32643", world_file=WORLD,
        geotransform_source="Supplier sheet", reason="Entered from the supplier's sheet.",
    )  # fmt: skip
    assert other.status_code == 201
    stale = apply(client, headers, asset, reviewed)
    assert stale.status_code == 409 and stale.json()["code"] == "GEOREFERENCE_VERSION_CONFLICT"

    fresh = review(client, asset)
    assert fresh["state"] == "in_use"  # the hand-entered values are the same as the sidecars'
    added(client, project, headers, "scan.aux.xml", aux("<FancyThing/>"))
    changed = apply(client, headers, asset, fresh)
    assert changed.status_code == 409 and changed.json()["code"] == "SIDECARS_CHANGED"
    assert state(client, asset)["current"]["version"] == 1


def test_a_changed_stored_sidecar_is_detected_and_not_used(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    world = added(client, project, headers, "scan.tfw", WORLD)
    added(client, project, headers, "scan.prj", PRJ_UTM43)
    reviewed = review(client, asset)
    path = store.absolute_path(store.relative_path(world["sha256"]))
    os.chmod(path, stat.S_IWRITE)
    path.write_text("10\n0\n0\n-10\n600005\n2999995\n")
    try:
        assert client.get(f"/api/v1/assets/{asset['id']}/sidecars").json()["code"] == "SIDECAR_CHANGED"
        refused = apply(client, headers, asset, reviewed)
        assert refused.status_code == 409 and refused.json()["code"] == "SIDECAR_CHANGED"
        assert state(client, asset)["current"]["version"] == 0
    finally:
        path.write_text(WORLD)


def test_a_changed_original_is_detected_through_the_sidecar_workflow(client, project, headers, run_jobs):
    asset, body = raster(client, project, headers, run_jobs, **UNPLACED)
    added(client, project, headers, "scan.tfw", WORLD)
    added(client, project, headers, "scan.prj", PRJ_UTM43)
    reviewed = review(client, asset)
    path = store.absolute_path(store.relative_path(hashlib.sha256(body).hexdigest()))
    os.chmod(path, stat.S_IWRITE)
    path.write_bytes(body + b"x")
    try:
        refused = apply(client, headers, asset, reviewed)
        assert refused.status_code == 409 and refused.json()["code"] == "ORIGINAL_CHANGED"
        assert state(client, asset)["current"]["version"] == 0
    finally:
        path.write_bytes(body)


def test_sidecar_records_cannot_be_rewritten(client, project, headers, run_jobs):
    raster(client, project, headers, run_jobs, **UNPLACED)
    sidecar = added(client, project, headers, "scan.tfw", WORLD)
    with get_engine().begin() as conn, pytest.raises(DBAPIError, match="append-only|append only"):
        conn.execute(text("UPDATE sidecar SET original_filename = 'x.tfw' WHERE id = :id"), {"id": sidecar["id"]})
    with get_engine().begin() as conn, pytest.raises(DBAPIError, match="append-only|append only"):
        conn.execute(text("DELETE FROM sidecar WHERE id = :id"), {"id": sidecar["id"]})


def test_a_new_version_from_sidecars_replaces_cached_tiles(client, project, headers, run_jobs):
    asset, _ = raster(client, project, headers, run_jobs, **UNPLACED)
    centre = TO_LONLAT.transform(501000, 2999250)
    assert client.get(f"/api/v1/assets/{asset['id']}/tiles/15/0/0.png").status_code == 409  # not placed: no tiles
    added(client, project, headers, "scan.tfw", WORLD)
    added(client, project, headers, "scan.prj", PRJ_UTM43)
    assert apply(client, headers, asset, review(client, asset)).status_code == 201
    assert alpha_at(client, asset, *centre) == 255  # drawn with version 1 (and now cached)
    # Version 2 moves the raster 50 km east by hand; the same tile address must not reuse version 1's tile.
    moved = record(
        client, headers, asset, expected_version=1, operation="set_geotransform",
        geotransform=[550000.0, 10.0, 0.0, 3000000.0, 0.0, -10.0], geotransform_source="Corrected supplier sheet",
        reason="The supplier corrected the easting.",
    )  # fmt: skip
    assert moved.status_code == 201, moved.text
    assert alpha_at(client, asset, *centre) == 0
    assert alpha_at(client, asset, *TO_LONLAT.transform(551000, 2999250)) == 255
    (layer,) = layers(client, project)["rasters"]
    assert layer["tile_url"].endswith("?v=2")


def test_gdal_never_reads_or_writes_pam_files(client, project, headers, run_jobs):
    assert os.environ["GDAL_PAM_ENABLED"] == "NO"
    asset, _ = raster(client, project, headers, run_jobs, size=(200, 150))
    run_jobs()  # statistics and preview read the original
    client.get(f"/api/v1/assets/{asset['id']}/render.png")
    assert list(get_settings().data_root.rglob("*.aux.xml")) == []


def test_names_are_paired_by_the_documented_rules():
    assert sidecars.naming("a.b.TFW") == sidecars.Naming("world_file", "a.b", None)
    assert sidecars.naming("x.tif.aux.xml") == sidecars.Naming("aux_xml", "x", "x.tif")
    assert sidecars.naming("x.aux.xml") == sidecars.Naming("aux_xml", "x", None)
    assert sidecars.naming(".prj") is None and sidecars.naming("x.txt") is None
    assert sidecars.pairs_with(sidecars.naming("x.aux.xml"), "X.tiff")
    assert not sidecars.pairs_with(sidecars.naming("x.tif.aux.xml"), "x.tiff")
    assert not sidecars.pairs_with(sidecars.naming("x.tfw"), "x.png")
