"""What the photo workspace and the multi-photo comparison rely on, as the existing API provides it.

The workspace adds no endpoint: it reads assets, photo locations and observations. These tests pin the properties the
comparison needs: a capture time is stored only when it is unambiguous and is otherwise null with a reason, an EXIF GPS
position stays an unconfirmed candidate without coordinates until a person confirms it, a project only ever sees its
own data, and reading changes nothing and links nothing across photographs.

Scratch database geopoc_test and scratch data folder only (see conftest.py).
"""

import json

from sqlalchemy import text

from geopoc.db.session import get_engine
from tests import media
from tests.test_assets import rows, upload
from tests.test_ground_pipeline import STUB, analyze
from tests.test_photo_locations import change, photo_with

UNKNOWN_UUID = "00000000-0000-4000-8000-00000000dead"


def project_of(client, headers, name):
    response = client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def timed_photo(client, project, headers, name, *, original, offset, seed):
    exif = media.exif_block(original=original, offset=offset)
    data = media.image_bytes("JPEG", seed=seed, exif=exif)
    return upload(client, project, headers, name, data).json()["asset"]


def listing(client, project):
    response = client.get(f"/api/v1/projects/{project['id']}/photo-locations")
    assert response.status_code == 200, response.text
    return {item["filename"]: item for item in response.json()["items"]}


def counts():
    with get_engine().connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in (
                "audit_event",
                "job",
                "photo_location",
                "observation",
                "observation_revision",
                "link_group",
                "asset",
            )
        }


# --- capture time ------------------------------------------------------------------------------------------------------


def test_capture_time_is_stored_only_when_it_is_unambiguous_and_otherwise_null_with_a_reason(client, project, headers):
    stated = timed_photo(
        client, project, headers, "stated.jpg", original="2026:10:01 10:00:00", offset="+05:30", seed=1
    )
    ambiguous = timed_photo(
        client, project, headers, "ambiguous.jpg", original="2026:10:01 10:00:00", offset=None, seed=2
    )
    none = upload(client, project, headers, "none.jpg", media.image_bytes("JPEG", seed=3)).json()["asset"]

    assert stated["acquisition_time"] == "2026-10-01T04:30:00Z"  # 10:00 at +05:30, stored in UTC
    assert "acquisition_time" not in stated["unknowns"]

    assert ambiguous["acquisition_time"] is None
    assert "no UTC offset" in ambiguous["unknowns"]["acquisition_time"]
    assert "TIMESTAMP_UNKNOWN" in ambiguous["quality_flags"]

    assert none["acquisition_time"] is None
    assert none["unknowns"]["acquisition_time"] == "The file carries no capture time."

    # the listing the workspace reads gives the same facts
    items = {a["original_filename"]: a for a in client.get(f"/api/v1/projects/{project['id']}/assets").json()["items"]}
    assert items["ambiguous.jpg"]["acquisition_time"] is None
    assert items["stated.jpg"]["acquisition_time"] == stated["acquisition_time"]


# --- locations ---------------------------------------------------------------------------------------------------------


def test_an_unconfirmed_gps_candidate_stays_unconfirmed_and_shows_no_coordinates(client, project, headers):
    candidate, _, _ = photo_with(client, project, headers, name="candidate.jpg", seed=1)
    confirmed, _, _ = photo_with(client, project, headers, name="confirmed.jpg", seed=2)
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG", seed=3)).json()["asset"]
    assert change(client, headers, confirmed["id"], action="confirm_exif", expected_version=1).status_code == 201

    items = listing(client, project)
    assert (
        items["candidate.jpg"]["state"],
        items["candidate.jpg"]["latitude"],
        items["candidate.jpg"]["longitude"],
    ) == (
        "candidate",
        None,
        None,
    )
    assert (
        items["confirmed.jpg"]["state"],
        items["confirmed.jpg"]["latitude"],
        items["confirmed.jpg"]["longitude"],
    ) == (
        "confirmed",
        28.6,
        77.2,
    )
    assert (items["plain.jpg"]["state"], items["plain.jpg"]["latitude"]) == ("none", None)
    # reading the listing again, and reading the photo, does not confirm anything
    listing(client, project)
    client.get(f"/api/v1/assets/{candidate['id']}/location")
    assert listing(client, project)["candidate.jpg"]["state"] == "candidate"
    assert plain["id"]


# --- project isolation and errors ----------------------------------------------------------------------------------------


def test_a_project_sees_only_its_own_photographs_locations_and_findings(client, project, headers, run_jobs):
    mine = upload(client, project, headers, "mine.jpg", media.image_bytes("JPEG", seed=1)).json()["asset"]
    other = project_of(client, headers, "Other")
    theirs, _, _ = photo_with(client, other, headers, name="theirs.jpg", seed=2)
    change(client, headers, theirs["id"], action="confirm_exif", expected_version=1)
    run_jobs()
    assert analyze(client, other, headers, theirs["id"]).status_code == 202
    run_jobs()

    assert set(listing(client, project)) == {"mine.jpg"}
    assert set(listing(client, other)) == {"theirs.jpg"}
    assets = client.get(f"/api/v1/projects/{project['id']}/assets").json()["items"]
    assert [a["original_filename"] for a in assets] == ["mine.jpg"]
    # the other project's photograph, asked for through this project, yields nothing
    through_mine = client.get(f"/api/v1/projects/{project['id']}/observations", params={"asset_id": theirs["id"]})
    assert through_mine.status_code == 200 and through_mine.json() == []
    through_theirs = client.get(
        f"/api/v1/projects/{other['id']}/observations", params={"asset_id": theirs["id"]}
    ).json()
    assert through_theirs and all(o["observation"]["project_id"] == other["id"] for o in through_theirs)
    assert mine["id"] not in json.dumps(through_theirs)


def test_unknown_projects_and_photographs_are_clear_404s(client, headers):
    for url in (
        f"/api/v1/projects/{UNKNOWN_UUID}/photo-locations",
        f"/api/v1/projects/{UNKNOWN_UUID}/assets",
        f"/api/v1/assets/{UNKNOWN_UUID}",
        f"/api/v1/assets/{UNKNOWN_UUID}/location",
    ):
        response = client.get(url, headers=headers)
        assert response.status_code == 404, url
        assert response.json()["code"].endswith("NOT_FOUND"), url
    observed = client.get(f"/api/v1/projects/{UNKNOWN_UUID}/observations")
    assert observed.status_code in (200, 404)
    if observed.status_code == 200:
        assert observed.json() == []  # nothing is invented for a project that does not exist


# --- reading changes nothing and links nothing ----------------------------------------------------------------------------


def test_reading_for_the_workspace_writes_nothing(client, project, headers, run_jobs):
    asset, _, _ = photo_with(client, project, headers, name="a.jpg", seed=1)
    run_jobs()
    assert analyze(client, project, headers, asset["id"]).status_code == 202
    run_jobs()
    before = counts()
    for _ in range(2):
        client.get(f"/api/v1/projects/{project['id']}/assets")
        client.get(f"/api/v1/projects/{project['id']}/photo-locations")
        client.get(f"/api/v1/projects/{project['id']}/observations", params={"asset_id": asset["id"]})
        client.get(f"/api/v1/assets/{asset['id']}")
        client.get(f"/api/v1/assets/{asset['id']}/location")
    assert counts() == before


def test_the_same_model_on_two_photographs_creates_no_link_between_them(client, project, headers, run_jobs):
    first = upload(client, project, headers, "one.jpg", media.image_bytes("JPEG", seed=1)).json()["asset"]
    second = upload(client, project, headers, "two.jpg", media.image_bytes("JPEG", seed=2)).json()["asset"]
    run_jobs()
    for asset in (first, second):
        assert analyze(client, project, headers, asset["id"], model=STUB).status_code == 202
    run_jobs()
    items = client.get(f"/api/v1/projects/{project['id']}/observations").json()
    assert {i["asset_filename"] for i in items} == {"one.jpg", "two.jpg"}
    assert all(i["possible_duplicate_group_ids"] == [] and i["open_conflict_ids"] == [] for i in items)
    assert rows("SELECT count(*) FROM link_group")[0][0] == 0
    assert rows("SELECT count(*) FROM conflict")[0][0] == 0
