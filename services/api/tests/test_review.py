"""Phase 4: human review with revision history, conflicts, possible-duplicate links and exports.

Observations are produced by the real ground-image pipeline running the test-only stub detectors, so these tests
exercise the stored path end to end. They say nothing about any real model's quality.
"""

import csv
import io
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from geopoc.db.session import get_engine
from tests.test_assets import upload
from tests.test_ground_pipeline import STUB, analyze, photo

ALT = {"name": "stub-detector-alt", "version": "test"}


def listing(client, project, **params):
    response = client.get(f"/api/v1/projects/{project['id']}/observations", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def by_category(items, category):
    return next(item for item in items if item["current"]["category"] == category)


def act(client, headers, observation_id, action, expected_revision, **body):
    return client.patch(
        f"/api/v1/observations/{observation_id}/review",
        json={"action": action, "expected_revision": expected_revision, **body},
        headers=headers,
    )


@pytest.fixture
def analysed(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    analyze(client, project, headers, asset["id"])
    assert run_jobs() == ["needs_review"]
    return asset


# --- review --------------------------------------------------------------------------------------------------------


def test_confirm_records_a_revision_and_keeps_the_model_output(client, project, headers, analysed, profile):
    item = by_category(listing(client, project), "test:thing a")
    original = item["observation"]
    assert item["current"] == {
        "status": "needs_review",
        "category": "test:thing a",
        "relabelled": False,
        "revision": 0,
        "last_changed_at": None,
        "last_changed_by": None,
        "last_note": None,
    }
    offered = {a["action"]: a for a in item["allowed_actions"]}
    assert set(offered) == {"confirm", "reject", "mark_inconclusive", "relabel", "note"}
    assert offered["reject"]["note_required"] and not offered["confirm"]["note_required"]

    response = act(client, headers, original["observation_id"], "confirm", 0)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["current"]["status"] == "verified"
    assert detail["current"]["revision"] == 1
    assert detail["current"]["last_changed_by"] == profile["display_name"]
    # The model's output is exactly as before: it is evidence, not the reviewed state.
    assert detail["observation"] == original
    assert detail["observation"]["review"]["status"] == "needs_review"
    (revision,) = detail["revisions"]
    assert revision["action"] == "confirm" and revision["revision_no"] == 1
    assert (revision["status_before"], revision["status_after"]) == ("needs_review", "verified")
    assert revision["actor_kind"] == "reviewer" and revision["actor_label"] == profile["display_name"]
    # A verified finding can only be reopened (with a reason), relabelled or annotated.
    assert {a["action"] for a in detail["allowed_actions"]} == {"reopen", "relabel", "note"}

    with get_engine().connect() as conn:
        audit = conn.execute(text("SELECT action, details FROM audit_event WHERE entity_type = 'observation'")).one()
    assert audit.action == "observation.confirm"
    assert audit.details["status"] == ["needs_review", "verified"]

    assert by_category(listing(client, project, status=["verified"]), "test:thing a")
    assert all(i["current"]["status"] != "verified" for i in listing(client, project, status=["needs_review"]))


def test_rejection_needs_a_reason_and_is_kept(client, project, headers, analysed):
    item = by_category(listing(client, project), "test:thing b")
    oid = item["observation"]["observation_id"]
    refused = act(client, headers, oid, "reject", 0)
    assert refused.status_code == 422 and refused.json()["code"] == "NOTE_REQUIRED"
    assert listing(client, project)[1]["current"]["revision"] == 0  # nothing was written

    done = act(client, headers, oid, "reject", 0, note="Shadow on the wall, not an object.")
    assert done.status_code == 200
    assert done.json()["current"]["status"] == "rejected"
    assert done.json()["current"]["last_note"] == "Shadow on the wall, not an object."
    # Rejected findings stay in the project and in exports.
    assert by_category(listing(client, project, status=["rejected"]), "test:thing b")
    assert {a["action"] for a in done.json()["allowed_actions"]} == {"reopen", "note"}

    reopened = act(client, headers, oid, "reopen", 1)
    assert reopened.status_code == 422 and reopened.json()["code"] == "NOTE_REQUIRED"
    reopened = act(client, headers, oid, "reopen", 1, note="Second look requested.")
    assert reopened.status_code == 200 and reopened.json()["current"]["status"] == "needs_review"
    assert [r["action"] for r in reopened.json()["revisions"]] == ["reject", "reopen"]


def test_state_rules_are_enforced(client, project, headers, analysed):
    oid = by_category(listing(client, project), "test:thing a")["observation"]["observation_id"]
    assert act(client, headers, oid, "confirm", 0).status_code == 200
    # verified -> rejected is not a permitted transition; the finding must be reopened first.
    refused = act(client, headers, oid, "reject", 1, note="changed my mind")
    assert refused.status_code == 422 and refused.json()["code"] == "INVALID_TRANSITION"
    again = act(client, headers, oid, "confirm", 1)
    assert again.status_code == 422 and again.json()["code"] == "NO_CHANGE"


def test_concurrent_reviews_conflict_instead_of_losing_an_update(client, project, headers, analysed):
    """PT-18: two reviewers act on the same revision; the second is refused and must reload."""
    other = client.post("/api/v1/workspace/profiles", json={"display_name": "Second Reviewer"}).json()
    other_headers = {"X-Workspace-Profile": other["id"]}
    oid = by_category(listing(client, project), "test:thing a")["observation"]["observation_id"]
    assert act(client, headers, oid, "confirm", 0).status_code == 200
    late = act(client, other_headers, oid, "mark_inconclusive", 0, note="Too dark to tell.")
    assert late.status_code == 409
    assert late.json()["code"] == "REVISION_CONFLICT"
    assert "revision 1" in late.json()["detail"]
    detail = client.get(f"/api/v1/observations/{oid}").json()
    assert detail["current"]["status"] == "verified" and len(detail["revisions"]) == 1


def test_relabel_changes_the_current_category_only(client, project, headers, analysed):
    item = by_category(listing(client, project), "test:thing a")
    oid = item["observation"]["observation_id"]
    no_reason = act(client, headers, oid, "relabel", 0, category="bicycle")
    assert no_reason.status_code == 422 and no_reason.json()["code"] == "NOTE_REQUIRED"
    empty = act(client, headers, oid, "relabel", 0, category="  ", note="x")
    assert empty.status_code == 422 and empty.json()["code"] == "CATEGORY_INVALID"

    done = act(client, headers, oid, "relabel", 0, category="bicycle", note="Wheels and frame are visible.")
    assert done.status_code == 200
    current = done.json()["current"]
    assert (current["category"], current["relabelled"], current["status"]) == ("bicycle", True, "needs_review")
    assert done.json()["observation"]["claim"]["category"] == "test:thing a"  # the model's label is kept
    assert [i["observation"]["observation_id"] for i in listing(client, project, category="bicycle")] == [oid]


def test_note_and_profile_requirements(client, project, headers, analysed):
    oid = listing(client, project)[0]["observation"]["observation_id"]
    anonymous = client.patch(f"/api/v1/observations/{oid}/review", json={"action": "confirm", "expected_revision": 0})
    assert anonymous.status_code == 401 and anonymous.json()["code"] == "PROFILE_REQUIRED"
    blank = act(client, headers, oid, "note", 0, note="   ")
    assert blank.status_code == 422 and blank.json()["code"] == "NOTE_REQUIRED"
    noted = act(client, headers, oid, "note", 0, note="Check against the second photo.")
    assert noted.status_code == 200 and noted.json()["current"]["status"] == "needs_review"
    unknown = client.get("/api/v1/observations/00000000-0000-4000-8000-000000000000")
    assert unknown.status_code == 404 and unknown.json()["code"] == "OBSERVATION_NOT_FOUND"


def test_the_database_refuses_to_change_model_output_or_history(client, project, headers, analysed):
    """T-11 / CL-09: the original output stays available whatever the application does."""
    oid = listing(client, project)[0]["observation"]["observation_id"]
    act(client, headers, oid, "note", 0, note="kept")
    statements = [
        'UPDATE observation SET record = record || \'{"summary": "edited"}\' WHERE id = :id',
        "UPDATE observation SET category = 'edited' WHERE id = :id",
        "UPDATE observation SET score_raw = 0.01 WHERE id = :id",
        "UPDATE observation_revision SET note = 'rewritten' WHERE observation_id = :id",
        "DELETE FROM observation_revision WHERE observation_id = :id",
    ]
    for statement in statements:
        with pytest.raises(DBAPIError, match="cannot be changed|append-only"):
            with get_engine().begin() as conn:
                conn.execute(text(statement), {"id": oid})
    with get_engine().begin() as conn:  # the current state may change; that is what review does
        conn.execute(text("UPDATE observation SET review_status = review_status WHERE id = :id"), {"id": oid})


# --- conflicts and possible duplicates -------------------------------------------------------------------------------


def test_disagreeing_runs_create_a_conflict_and_agreeing_ones_a_candidate_link(client, project, headers, run_jobs):
    """T-07 / CL-07: both findings are kept, a conflict record is created, and both become disputed."""
    asset = photo(client, project, headers, run_jobs)
    analyze(client, project, headers, asset["id"])
    run_jobs()
    queued = analyze(client, project, headers, asset["id"], model=ALT).json()
    assert run_jobs() == ["needs_review"]
    result = client.get(f"/api/v1/jobs/{queued['id']}").json()["result"]
    assert result["conflicts"] == 1 and result["possible_duplicate_links"] == 1

    items = listing(client, project)
    assert len(items) == 4  # nothing is merged or dropped
    a, z = by_category(items, "test:thing a"), by_category(items, "test:thing z")
    assert a["current"]["status"] == z["current"]["status"] == "disputed"
    assert a["open_conflict_ids"] == z["open_conflict_ids"] and len(a["open_conflict_ids"]) == 1
    b_items = [i for i in items if i["current"]["category"] == "test:thing b"]
    assert len(b_items) == 2
    assert b_items[0]["possible_duplicate_group_ids"] == b_items[1]["possible_duplicate_group_ids"] != []
    assert all(i["current"]["status"] == "needs_review" for i in b_items)  # a link is not a conclusion

    (conflict,) = client.get(f"/api/v1/projects/{project['id']}/conflicts").json()
    assert conflict["attribute"] == "category" and conflict["status"] == "open"
    assert sorted(m["value"]["category"] for m in conflict["members"]) == ["test:thing a", "test:thing z"]
    assert conflict["rule"] == "pixel_overlap_iou_0.5_v1"
    assert [i["observation"]["observation_id"] for i in listing(client, project, in_conflict=True)] != []
    assert len(listing(client, project, in_conflict=False)) == 2

    detail = client.get(f"/api/v1/observations/{a['observation']['observation_id']}").json()
    (dispute,) = detail["revisions"]
    assert dispute["action"] == "system_dispute" and dispute["actor_kind"] == "system"
    assert "conflict detection" in dispute["actor_label"]
    # Only a person may conclude a dispute, and always with a reason.
    offered = {x["action"]: x["note_required"] for x in detail["allowed_actions"]}
    assert offered["confirm"] and offered["reject"] and offered["reopen"]
    assert "mark_inconclusive" not in offered

    zid, aid = z["observation"]["observation_id"], a["observation"]["observation_id"]
    assert act(client, headers, zid, "confirm", 1).json()["code"] == "NOTE_REQUIRED"
    assert act(client, headers, zid, "confirm", 1, note="Label z is right.").status_code == 200
    assert client.get(f"/api/v1/projects/{project['id']}/conflicts", params={"status": "open"}).json() != []
    assert act(client, headers, aid, "reject", 1, note="Label a is wrong.").status_code == 200
    (resolved,) = client.get(f"/api/v1/projects/{project['id']}/conflicts").json()
    assert resolved["status"] == "resolved_documented" and "Label a is wrong." in resolved["resolution_note"]
    assert {m["current_status"] for m in resolved["members"]} == {"verified", "rejected"}
    assert len(listing(client, project)) == 4


def test_copies_of_an_image_are_linked_not_counted_twice(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    copy = upload(client, project, headers, "copy.jpg", _bytes_of(client, asset)).json()["asset"]
    run_jobs()
    for target in (asset, copy):
        analyze(client, project, headers, target["id"])
        run_jobs()
    items = listing(client, project)
    assert len(items) == 4 and all(i["possible_duplicate_group_ids"] for i in items)
    assert all(i["current"]["status"] == "needs_review" for i in items)
    assert client.get(f"/api/v1/projects/{project['id']}/conflicts").json() == []


def _bytes_of(client, asset) -> bytes:
    return client.get(f"/api/v1/assets/{asset['id']}/content").content


def test_rejected_findings_take_no_part_in_conflicts(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    analyze(client, project, headers, asset["id"])
    run_jobs()
    a = by_category(listing(client, project), "test:thing a")
    act(client, headers, a["observation"]["observation_id"], "reject", 0, note="Not an object.")
    analyze(client, project, headers, asset["id"], model=ALT)
    run_jobs()
    assert client.get(f"/api/v1/projects/{project['id']}/conflicts").json() == []
    assert by_category(listing(client, project), "test:thing z")["current"]["status"] == "needs_review"


# --- exports ---------------------------------------------------------------------------------------------------------


def test_csv_export_labels_pixel_columns_and_states_review(client, project, headers, analysed):
    oid = by_category(listing(client, project), "test:thing a")["observation"]["observation_id"]
    act(client, headers, oid, "confirm", 0)
    url = f"/api/v1/projects/{project['id']}/exports/observations.csv"
    assert client.get(url).status_code == 401
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header, body = rows[0], rows[1:]
    assert "px_x_min (source image pixels, top-left origin)" in header
    assert "raw_score (model's own value, not a probability)" in header
    assert "geometry_crs (empty: no geographic location)" in header
    assert not any(column.lower() in ("lat", "lon", "latitude", "longitude", "x", "y") for column in header)
    assert len(body) == 2
    record = dict(zip(header, body[0], strict=True))
    assert record["review_status (current)"] == "verified"
    assert record["model_category (as the model output it)"] == "test:thing a"
    assert record["geometry_crs (empty: no geographic location)"] == ""
    verified_only = client.get(url, params={"status": ["verified"]}, headers=headers)
    assert len(list(csv.reader(io.StringIO(verified_only.content.decode("utf-8-sig"))))) == 2


def test_geojson_export_of_pixel_only_observations_has_no_features(client, project, headers, analysed):
    """PT-16 / CL-13: pixel positions are never written as geographic coordinates."""
    response = client.get(f"/api/v1/projects/{project['id']}/exports/observations.geojson", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/geo+json"
    document = response.json()
    assert document["type"] == "FeatureCollection" and document["features"] == []
    assert document["excluded"]["count"] == 2
    assert "no established geographic location" in document["excluded"]["reason"]


def test_json_export_carries_original_output_history_and_provenance(client, project, headers, analysed):
    oid = by_category(listing(client, project), "test:thing a")["observation"]["observation_id"]
    act(client, headers, oid, "relabel", 0, category="bicycle", note="Visible frame.")
    response = client.get(f"/api/v1/projects/{project['id']}/exports/observations.json", headers=headers)
    assert response.status_code == 200
    document = json.loads(response.content)
    exported = next(o for o in document["observations"] if o["observation"]["observation_id"] == oid)
    assert exported["observation"]["claim"]["category"] == "test:thing a"
    assert exported["current"]["category"] == "bicycle"
    assert [r["action"] for r in exported["revisions"]] == ["relabel"]
    (run,) = document["runs"]
    assert run["model"] == "stub-detector test" and run["job_id"]
    with get_engine().connect() as conn:
        formats = (
            conn.execute(text("SELECT details ->> 'format' FROM audit_event WHERE action = 'export.observations'"))
            .scalars()
            .all()
        )
    assert formats == ["json"]


def test_exports_of_an_empty_project_are_valid(client, project, headers):
    csv_body = client.get(f"/api/v1/projects/{project['id']}/exports/observations.csv", headers=headers)
    assert len(list(csv.reader(io.StringIO(csv_body.content.decode("utf-8-sig"))))) == 1  # header only
    geo = client.get(f"/api/v1/projects/{project['id']}/exports/observations.geojson", headers=headers).json()
    assert geo["features"] == [] and geo["excluded"]["count"] == 0
    missing = client.get(
        "/api/v1/projects/00000000-0000-4000-8000-000000000000/exports/observations.csv", headers=headers
    )
    assert missing.status_code == 404


def test_listing_is_unchanged_for_stub_used_in_ui(client, project, headers, analysed):
    item = listing(client, project, run_id=listing(client, project)[0]["observation"]["model"]["run_id"])[0]
    assert item["asset_filename"] == "photo.jpg"
    assert item["observation"]["model"] == {**STUB, "run_id": item["observation"]["model"]["run_id"]}
