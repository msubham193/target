"""Demo phase 1: photo GPS candidates, person-recorded photo locations, and their export.

Run against the separate test database and scratch data folder only (see conftest.py). Media is generated in memory.
"""

import hashlib
import json

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from geopoc.db.session import get_engine
from geopoc.ingestion.gps import read_gps
from tests import media
from tests.test_assets import rows, upload
from tests.test_ground_pipeline import STUB, analyze

NAN = float("nan")


# --- reading the EXIF GPS block -------------------------------------------------------------------------------------


def test_a_valid_position_is_read_as_a_candidate():
    reading = read_gps(media.GpsBlock())
    assert reading.state == "candidate"
    assert (reading.latitude, reading.longitude) == (28.6, 77.2)
    assert reading.details["datum"].startswith("not declared")
    assert reading.problems == ()


def test_southern_and_western_references_give_negative_coordinates():
    reading = read_gps(media.GpsBlock(t1="S", t3="W", t2=(33.0, 52.0, 30.0), t4=(151.0, 12.0, 36.0)))
    assert reading.state == "candidate"
    assert reading.latitude == pytest.approx(-(33 + 52 / 60 + 30 / 3600), abs=1e-7)
    assert reading.longitude == pytest.approx(-(151 + 12 / 60 + 36 / 3600), abs=1e-7)


def test_a_photograph_without_a_gps_block_has_no_candidate():
    assert read_gps(Image.Exif()).state == "absent"
    assert read_gps(media.exif_block(original="2026:01:02 03:04:05")).state == "absent"


def test_wgs84_datum_is_accepted_and_recorded():
    reading = read_gps(media.GpsBlock(t18="WGS-84"))
    assert reading.state == "candidate" and reading.details["datum"] == "WGS-84"


@pytest.mark.parametrize(
    ("tags", "problem"),
    [
        ({"t4": None}, "GPS_INCOMPLETE"),
        ({"t3": None}, "GPS_INCOMPLETE"),
        ({"t2": None, "t4": None}, "GPS_INCOMPLETE"),
        ({"t2": (28.0, 36.0)}, "GPS_MALFORMED"),
        ({"t4": "77.2"}, "GPS_MALFORMED"),
        ({"t1": "X"}, "GPS_REFERENCE_INVALID"),
        ({"t3": "Q"}, "GPS_REFERENCE_INVALID"),
        ({"t2": (91.0, 0.0, 0.0)}, "GPS_OUT_OF_RANGE"),
        ({"t4": (181.0, 0.0, 0.0)}, "GPS_OUT_OF_RANGE"),
        ({"t2": (28.0, 75.0, 0.0)}, "GPS_OUT_OF_RANGE"),
        ({"t2": (-28.0, 0.0, 0.0)}, "GPS_OUT_OF_RANGE"),
        ({"t2": (NAN, 0.0, 0.0)}, "GPS_NOT_FINITE"),
        ({"t4": (float("inf"), 0.0, 0.0)}, "GPS_NOT_FINITE"),
        ({"t2": (0.0, 0.0, 0.0), "t4": (0.0, 0.0, 0.0)}, "GPS_ZERO_COORDINATE"),
        ({"t9": "V"}, "GPS_VOID"),
        ({"t18": "TOKYO"}, "GPS_DATUM_NOT_WGS84"),
    ],
)
def test_an_unusable_gps_block_is_not_a_candidate(tags, problem):
    reading = read_gps(media.GpsBlock(**tags))
    assert reading.state == "unusable"
    assert problem in reading.problems
    assert reading.latitude is None and reading.longitude is None


def test_a_damaged_gps_block_does_not_raise(monkeypatch):
    exif = media.GpsBlock()

    def broken(_tag):
        raise RuntimeError("damaged IFD")

    monkeypatch.setattr(exif, "get_ifd", broken)
    reading = read_gps(exif)
    assert reading.state == "unusable" and reading.problems == ("GPS_UNREADABLE",)


# --- upload ---------------------------------------------------------------------------------------------------------


def photo_with(client, project, headers, name="gps.jpg", fmt="JPEG", seed=1, exif=None):
    data = media.image_bytes(fmt, seed=seed, exif=exif if exif is not None else media.gps_exif())
    response = upload(client, project, headers, name, data)
    assert response.status_code == 201, response.text
    return response.json()["asset"], data, response


def location(client, asset_id):
    response = client.get(f"/api/v1/assets/{asset_id}/location")
    assert response.status_code == 200, response.text
    return response.json()


def change(client, headers, asset_id, **body):
    body.setdefault("confirm", True)
    return client.post(f"/api/v1/assets/{asset_id}/location", json=body, headers=headers)


def test_upload_keeps_the_gps_position_as_an_unverified_candidate(client, project, headers):
    asset, data, response = photo_with(client, project, headers)
    # The original is stored untouched, byte for byte.
    assert asset["sha256"] == hashlib.sha256(data).hexdigest()
    # The asset record says a candidate exists but carries no coordinates and no other EXIF GPS content.
    assert asset["metadata"]["exif_gps"] == {"state": "candidate", "problems": []}
    assert "unverified candidate" in asset["unknowns"]["geolocation"]
    assert "NOT_GEOREFERENCED" in asset["quality_flags"]
    assert "28.6" not in response.text and "77.2" not in response.text
    state = location(client, asset["id"])
    assert state["current"]["state"] == "none" and state["current"]["latitude"] is None
    assert state["exif"]["state"] == "candidate"
    assert (state["exif"]["latitude"], state["exif"]["longitude"]) == (28.6, 77.2)
    assert [(r["status"], r["source"], r["actor_label"]) for r in state["history"]] == [
        ("candidate", "exif", "Test Reviewer")
    ]


def test_a_candidate_is_not_a_location_on_the_map_or_in_the_export(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    listing = client.get(f"/api/v1/projects/{project['id']}/photo-locations").json()
    (item,) = listing["items"]
    assert (item["state"], item["latitude"], item["longitude"]) == ("candidate", None, None)
    assert (listing["confirmed"], listing["candidates"], listing["without_location"]) == (0, 1, 0)
    geo = client.get(f"/api/v1/projects/{project['id']}/exports/observations.geojson", headers=headers).json()
    assert geo["features"] == []
    assert geo["photographs"]["located"] == 0 and geo["photographs"]["unconfirmed_exif_candidates"] == 1
    assert asset["id"] not in json.dumps(geo["features"])


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_png_and_webp_gps_is_read_when_the_exif_chunk_is_present(client, project, headers, fmt):
    """Synthetic files written by Pillow. No real-device PNG or WebP sample was available to test against."""
    asset, _, _ = photo_with(client, project, headers, name=f"gps.{fmt.lower()}", fmt=fmt)
    assert location(client, asset["id"])["exif"]["state"] == "candidate"


@pytest.mark.parametrize(
    ("tags", "problem"),
    [({"t4": None}, "GPS_INCOMPLETE"), ({"t9": "V"}, "GPS_VOID"), ({"t18": "TOKYO"}, "GPS_DATUM_NOT_WGS84")],
)
def test_upload_with_unusable_gps_stores_no_candidate(client, project, headers, tags, problem):
    asset, _, _ = photo_with(client, project, headers, exif=media.gps_exif(**tags))
    assert asset["metadata"]["exif_gps"] == {"state": "unusable", "problems": [problem]}
    assert "cannot be used" in asset["unknowns"]["geolocation"]
    state = location(client, asset["id"])
    assert state["exif"]["state"] == "unusable" and state["history"] == []
    assert [p["code"] for p in state["exif"]["problems"]] == [problem]
    assert rows("SELECT count(*) FROM photo_location")[0][0] == 0


def test_upload_without_exif_or_gps_is_unchanged(client, project, headers):
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG")).json()["asset"]
    no_gps = upload(
        client, project, headers, "no_gps.jpg", media.image_bytes("JPEG", seed=4, exif=media.exif_block())
    ).json()["asset"]
    for asset in (plain, no_gps):
        assert asset["status"] == "validated_with_warnings" or asset["status"] == "validated"
        assert "no location is inferred" in asset["unknowns"]["geolocation"]
        state = location(client, asset["id"])
        assert state["current"] == {
            "state": "none",
            "version_no": 0,
            "source": None,
            "latitude": None,
            "longitude": None,
            "crs_id": None,
            "reason": None,
            "actor_label": None,
            "changed_at": None,
        }
        assert state["exif"]["state"] == "none" and state["history"] == []


def test_a_malformed_exif_block_still_uploads(client, project, headers, monkeypatch):
    def broken(_image):
        raise RuntimeError("malformed")

    monkeypatch.setattr("geopoc.ingestion.image.read_gps", broken)
    response = upload(client, project, headers, "bad.jpg", media.image_bytes("JPEG", exif=media.gps_exif()))
    assert response.status_code == 201
    assert response.json()["asset"]["metadata"]["exif_gps"] == {"state": "unusable", "problems": ["GPS_UNREADABLE"]}


# --- confirming the candidate ---------------------------------------------------------------------------------------


def test_confirming_the_exif_candidate_records_who_when_and_where_it_came_from(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    response = change(client, headers, asset["id"], action="confirm_exif", expected_version=1, reason="Checked on site")
    assert response.status_code == 201, response.text
    body = response.json()
    now = body["current"]
    assert (now["state"], now["source"], now["latitude"], now["longitude"], now["version_no"]) == (
        "confirmed",
        "exif",
        28.6,
        77.2,
        2,
    )
    assert now["actor_label"] == "Test Reviewer" and now["reason"] == "Checked on site" and now["changed_at"]
    assert [(h["version_no"], h["status"], h["candidate_version_no"]) for h in body["history"]] == [
        (1, "candidate", None),
        (2, "confirmed", 1),
    ]
    (audit,) = rows("SELECT details FROM audit_event WHERE action = 'photo_location.confirm_exif'")
    assert audit[0] == {"version": 2, "status": "confirmed", "source": "exif"}
    listing = client.get(f"/api/v1/projects/{project['id']}/photo-locations").json()
    assert listing["items"][0]["state"] == "confirmed" and listing["items"][0]["confirmed_by"] == "Test Reviewer"
    assert (listing["items"][0]["latitude"], listing["items"][0]["longitude"]) == (28.6, 77.2)


def test_confirmation_must_be_explicit_and_current(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    unconfirmed = change(client, headers, asset["id"], action="confirm_exif", expected_version=1, confirm=False)
    assert unconfirmed.status_code == 422 and unconfirmed.json()["code"] == "CONFIRMATION_REQUIRED"
    stale = change(client, headers, asset["id"], action="confirm_exif", expected_version=0)
    assert stale.status_code == 409 and stale.json()["code"] == "LOCATION_VERSION_CONFLICT"
    anonymous = client.post(
        f"/api/v1/assets/{asset['id']}/location",
        json={"action": "confirm_exif", "expected_version": 1, "confirm": True},
    )
    assert anonymous.status_code == 401
    assert location(client, asset["id"])["current"]["state"] == "none"  # nothing was recorded by any of them


def test_there_is_nothing_to_confirm_without_a_candidate(client, project, headers):
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG")).json()["asset"]
    response = change(client, headers, plain["id"], action="confirm_exif", expected_version=0)
    assert response.status_code == 409 and response.json()["code"] == "NO_EXIF_CANDIDATE"


def test_confirming_twice_is_refused(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    assert change(client, headers, asset["id"], action="confirm_exif", expected_version=1).status_code == 201
    again = change(client, headers, asset["id"], action="confirm_exif", expected_version=2)
    assert again.status_code == 409 and again.json()["code"] == "LOCATION_UNCHANGED"


# --- manual entry and map selection ---------------------------------------------------------------------------------


def test_manual_coordinates_are_recorded_as_a_person_asserted_location(client, project, headers):
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG")).json()["asset"]
    response = change(
        client,
        headers,
        plain["id"],
        action="set_position",
        method="manual_entry",
        latitude=19.0760,
        longitude=72.8777,
        expected_version=0,
        reason="Site visit notes",
    )
    assert response.status_code == 201, response.text
    now = response.json()["current"]
    assert (now["state"], now["source"], now["latitude"], now["longitude"], now["crs_id"]) == (
        "confirmed",
        "manual_entry",
        19.076,
        72.8777,
        "EPSG:4326",
    )
    assert response.json()["exif"]["state"] == "none"


def test_a_point_chosen_on_the_map_is_recorded_with_that_source(client, project, headers):
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG")).json()["asset"]
    response = change(
        client, headers, plain["id"], action="set_position", method="map_selection", latitude=-33.8688,
        longitude=151.2093, expected_version=0,
    )  # fmt: skip
    assert response.status_code == 201
    assert response.json()["current"]["source"] == "map_selection"
    assert response.json()["history"][0]["status"] == "confirmed"


def test_a_manual_position_replaces_the_exif_candidate_and_keeps_it_in_the_history(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    response = change(
        client, headers, asset["id"], action="set_position", method="manual_entry", latitude=10.5,
        longitude=20.25, expected_version=1,
    )  # fmt: skip
    assert response.status_code == 201
    body = response.json()
    assert (body["current"]["latitude"], body["current"]["source"]) == (10.5, "manual_entry")
    assert [(h["status"], h["source"]) for h in body["history"]] == [
        ("candidate", "exif"),
        ("confirmed", "manual_entry"),
    ]
    assert (body["exif"]["state"], body["exif"]["latitude"]) == ("candidate", 28.6)  # still available to compare


@pytest.mark.parametrize(
    ("latitude", "longitude", "code"),
    [
        (91, 10, "LOCATION_LATITUDE_RANGE"),
        (-90.5, 10, "LOCATION_LATITUDE_RANGE"),
        (10, 180.5, "LOCATION_LONGITUDE_RANGE"),
        (10, -181, "LOCATION_LONGITUDE_RANGE"),
        (10, None, "LOCATION_INCOMPLETE"),
        (None, 10, "LOCATION_INCOMPLETE"),
        (0, 0, "LOCATION_NULL_ISLAND"),
    ],
)
def test_invalid_manual_positions_are_refused_and_nothing_is_stored(
    client, project, headers, latitude, longitude, code
):
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG")).json()["asset"]
    response = change(
        client, headers, plain["id"], action="set_position", method="manual_entry", latitude=latitude,
        longitude=longitude, expected_version=0,
    )  # fmt: skip
    assert response.status_code == 422 and response.json()["code"] == code
    assert rows("SELECT count(*) FROM photo_location")[0][0] == 0
    assert rows("SELECT count(*) FROM audit_event WHERE action = 'photo_location.refused'")[0][0] == 1


def test_non_numeric_and_non_finite_coordinates_are_refused(client, project, headers):
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG")).json()["asset"]
    url = f"/api/v1/assets/{plain['id']}/location"
    base = '{"action": "set_position", "method": "manual_entry", "expected_version": 0, "confirm": true, '
    for coordinates in (
        '"latitude": NaN, "longitude": 10',
        '"latitude": 10, "longitude": Infinity',
        '"latitude": "north", "longitude": 10',
    ):
        response = client.post(
            url, content=base + coordinates + "}", headers={**headers, "Content-Type": "application/json"}
        )
        assert response.status_code == 422, coordinates
    assert rows("SELECT count(*) FROM photo_location")[0][0] == 0


def test_the_method_is_required_for_a_position(client, project, headers):
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG")).json()["asset"]
    response = change(
        client, headers, plain["id"], action="set_position", latitude=10, longitude=20, expected_version=0
    )
    assert response.status_code == 422 and response.json()["code"] == "LOCATION_METHOD_REQUIRED"


def test_the_same_position_twice_is_refused(client, project, headers):
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG")).json()["asset"]
    args = {"action": "set_position", "method": "manual_entry", "latitude": 12.5, "longitude": 45.5}
    assert change(client, headers, plain["id"], expected_version=0, **args).status_code == 201
    again = change(client, headers, plain["id"], expected_version=1, **args)
    assert again.status_code == 409 and again.json()["code"] == "LOCATION_UNCHANGED"


def test_clearing_removes_the_location_but_keeps_the_history(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    assert change(client, headers, asset["id"], action="confirm_exif", expected_version=1).status_code == 201
    cleared = change(client, headers, asset["id"], action="clear", expected_version=2, reason="Wrong place")
    assert cleared.status_code == 201
    body = cleared.json()
    assert body["current"]["state"] == "none" and body["current"]["latitude"] is None
    assert [h["status"] for h in body["history"]] == ["candidate", "confirmed", "cleared"]
    assert body["history"][2]["reason"] == "Wrong place"
    listing = client.get(f"/api/v1/projects/{project['id']}/photo-locations").json()
    assert listing["items"][0]["state"] == "none" and listing["items"][0]["latitude"] is None
    nothing = change(client, headers, asset["id"], action="clear", expected_version=3)
    assert nothing.status_code == 409 and nothing.json()["code"] == "LOCATION_NOTHING_TO_CLEAR"
    # The candidate can be adopted again later; it was never destroyed.
    assert change(client, headers, asset["id"], action="confirm_exif", expected_version=3).status_code == 201


def test_only_ground_photographs_have_locations(client, project, headers):
    raster = upload(client, project, headers, "r.tif", media.geotiff_bytes(), "overhead_raster").json()["asset"]
    response = client.get(f"/api/v1/assets/{raster['id']}/location")
    assert response.status_code == 409 and response.json()["code"] == "NOT_A_PHOTOGRAPH"
    assert client.get("/api/v1/assets/00000000-0000-4000-8000-000000000000/location").status_code == 404


# --- reading the original again -------------------------------------------------------------------------------------


def test_read_exif_records_a_candidate_for_a_photograph_ingested_before_gps_was_read(
    client, project, headers, monkeypatch
):
    with monkeypatch.context() as patch:  # ingest as the earlier version did: GPS tags noted, position not read
        patch.setattr("geopoc.ingestion.image.read_gps", lambda _exif: read_gps(Image.Exif()))
        asset, data, _ = photo_with(client, project, headers)
    assert location(client, asset["id"])["history"] == []
    response = client.post(f"/api/v1/assets/{asset['id']}/location/read-exif", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "candidate_recorded"
    assert (body["location"]["exif"]["latitude"], body["location"]["current"]["state"]) == (28.6, "none")
    again = client.post(f"/api/v1/assets/{asset['id']}/location/read-exif", headers=headers)
    assert again.status_code == 409 and again.json()["code"] == "EXIF_CANDIDATE_EXISTS"
    assert hashlib.sha256(data).hexdigest() == asset["sha256"]


def test_read_exif_reports_a_photograph_with_no_gps_and_changes_nothing(client, project, headers):
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG")).json()["asset"]
    response = client.post(f"/api/v1/assets/{plain['id']}/location/read-exif", headers=headers)
    assert response.status_code == 200 and response.json()["outcome"] == "no_gps"
    assert rows("SELECT count(*) FROM photo_location")[0][0] == 0
    assert client.post(f"/api/v1/assets/{plain['id']}/location/read-exif").status_code == 401


def test_read_exif_never_replaces_a_confirmed_location(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    assert change(client, headers, asset["id"], action="confirm_exif", expected_version=1).status_code == 201
    response = client.post(f"/api/v1/assets/{asset['id']}/location/read-exif", headers=headers)
    assert response.status_code == 409 and response.json()["code"] == "LOCATION_ALREADY_CONFIRMED"
    assert location(client, asset["id"])["current"]["state"] == "confirmed"


# --- history is append-only, constraints hold -----------------------------------------------------------------------


def test_location_history_cannot_be_edited_or_deleted(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    for statement in ("UPDATE photo_location SET latitude = 1", "DELETE FROM photo_location"):
        with pytest.raises(DBAPIError), get_engine().begin() as conn:
            conn.execute(text(statement))
    assert location(client, asset["id"])["exif"]["latitude"] == 28.6


@pytest.mark.parametrize(
    "values",
    [
        "'candidate', 'manual_entry', 10, 10",  # a candidate comes only from the file
        "'candidate', 'exif', 91, 10",
        "'confirmed', 'exif', 10, 181",
        "'confirmed', 'exif', NULL, 10",
        "'confirmed', 'exif', 'NaN', 10",
        "'cleared', 'none', 10, 10",
        "'confirmed', 'none', 10, 10",
    ],
)
def test_the_database_refuses_impossible_location_rows(client, project, headers, values):
    asset, _, _ = photo_with(client, project, headers, exif=media.exif_block())
    with pytest.raises(DBAPIError), get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO photo_location (id, project_id, asset_id, version_no, status, source, latitude, longitude,"
                " source_checksum, actor_label, created_at) "
                f"SELECT gen_random_uuid(), project_id, id, 9, {values}, blob_sha256, 'x', now() FROM asset"
            )
        )


# --- observations and exports ---------------------------------------------------------------------------------------


def test_a_confirmed_location_does_not_georeference_the_findings(client, project, headers, run_jobs):
    asset, _, _ = photo_with(client, project, headers)
    run_jobs()
    assert analyze(client, project, headers, asset["id"], model=STUB).status_code == 202
    run_jobs()
    before = client.get(f"/api/v1/projects/{project['id']}/observations").json()
    assert before
    assert change(client, headers, asset["id"], action="confirm_exif", expected_version=1).status_code == 201
    after = client.get(f"/api/v1/projects/{project['id']}/observations").json()
    assert after == before
    for item in after:
        assert item["observation"]["geospatial"]["geometry"] is None
        assert "NOT_GEOREFERENCED" in item["observation"]["quality_flags"]


def test_geojson_includes_only_photographs_with_a_confirmed_location(client, project, headers, run_jobs):
    located, _, _ = photo_with(client, project, headers, name="located.jpg", seed=1)
    candidate_only, _, _ = photo_with(client, project, headers, name="candidate.jpg", seed=2)
    typed = upload(client, project, headers, "typed.jpg", media.image_bytes("JPEG", seed=3)).json()["asset"]
    unplaced = upload(client, project, headers, "unplaced.jpg", media.image_bytes("JPEG", seed=4)).json()["asset"]
    run_jobs()
    analyze(client, project, headers, located["id"], model=STUB)
    run_jobs()
    assert change(client, headers, located["id"], action="confirm_exif", expected_version=1).status_code == 201
    assert change(
        client, headers, typed["id"], action="set_position", method="map_selection", latitude=-12.5,
        longitude=130.75, expected_version=0,
    ).status_code == 201  # fmt: skip

    response = client.get(f"/api/v1/projects/{project['id']}/exports/observations.geojson", headers=headers)
    assert response.status_code == 200
    document = response.json()
    features = document["features"]
    assert {f["properties"]["source_file"] for f in features} == {"located.jpg", "typed.jpg"}
    by_file = {f["properties"]["source_file"]: f for f in features}
    assert by_file["located.jpg"]["geometry"] == {"type": "Point", "coordinates": [77.2, 28.6]}  # lon, lat
    assert by_file["typed.jpg"]["geometry"] == {"type": "Point", "coordinates": [130.75, -12.5]}
    for feature in features:
        lon, lat = feature["geometry"]["coordinates"]
        assert -180 <= lon <= 180 and -90 <= lat <= 90
        assert feature["properties"]["feature_type"] == "photo_location"
        assert feature["properties"]["location_status"] == "confirmed"
    props = by_file["located.jpg"]["properties"]
    assert props["location_source"] == "exif" and props["confirmed_by"] == "Test Reviewer"
    assert props["observations_total"] == 2 and props["observations_by_review_status"] == {"needs_review": 2}
    assert by_file["typed.jpg"]["properties"]["location_source"] == "map_selection"
    # Nothing is invented for the others: they are counted, never placed.
    assert document["photographs"]["located"] == 2 and document["photographs"]["not_located"] == 2
    assert document["photographs"]["unconfirmed_exif_candidates"] == 1
    assert candidate_only["id"] not in response.text and unplaced["id"] not in response.text
    # The observation count still describes observations only: the two findings in the located photograph.
    assert document["excluded"]["count"] == 2
    (audit,) = rows("SELECT details FROM audit_event WHERE action = 'export.observations'")
    assert audit[0]["photos_located"] == 2


def test_json_export_carries_the_location_history(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    assert change(client, headers, asset["id"], action="confirm_exif", expected_version=1).status_code == 201
    document = json.loads(
        client.get(f"/api/v1/projects/{project['id']}/exports/observations.json", headers=headers).content
    )
    (entry,) = document["photo_locations"]
    assert entry["asset_id"] == asset["id"] and entry["current_state"] == "confirmed"
    assert [(h["version_no"], h["status"], h["source"]) for h in entry["history"]] == [
        (1, "candidate", "exif"),
        (2, "confirmed", "exif"),
    ]


def test_csv_export_gains_no_coordinate_columns(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    change(client, headers, asset["id"], action="confirm_exif", expected_version=1)
    body = client.get(f"/api/v1/projects/{project['id']}/exports/observations.csv", headers=headers).content.decode(
        "utf-8-sig"
    )
    assert "latitude" not in body.lower() and "28.6" not in body


def test_exports_of_a_project_without_photographs_are_unchanged(client, project, headers):
    geo = client.get(f"/api/v1/projects/{project['id']}/exports/observations.geojson", headers=headers).json()
    assert geo["features"] == [] and geo["excluded"]["count"] == 0
    assert geo["photographs"]["located"] == 0 and geo["photographs"]["not_located"] == 0
    listing = client.get(f"/api/v1/projects/{project['id']}/photo-locations").json()
    assert listing["items"] == []
    assert client.get("/api/v1/projects/00000000-0000-4000-8000-000000000000/photo-locations").status_code == 404
