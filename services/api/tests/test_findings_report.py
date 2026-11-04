"""The findings report: a read-only document built only from what is stored for one project.

Observations come from the real ground-image pipeline running the test-only stub detectors, and everything runs against
the scratch database geopoc_test (see conftest.py). Nothing here says anything about a real model's quality.
"""

import json

from sqlalchemy import text

from geopoc.db.session import get_engine
from tests import media
from tests.test_assets import rows, upload
from tests.test_ground_pipeline import STUB, analyze, photo
from tests.test_photo_locations import change, photo_with
from tests.test_review import ALT, act, by_category, listing

HOSTILE = '<script>alert("x")</script> & <img src=x onerror=alert(1)> </script>'


def report(client, project, headers):
    response = client.get(f"/api/v1/projects/{project['id']}/report", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def new_project(client, headers, name):
    response = client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def analysed_photo(client, project, headers, run_jobs, name="photo.jpg", model=STUB, seed=1):
    asset = upload(client, project, headers, name, media.image_bytes("JPEG", size=(64, 48), seed=seed)).json()["asset"]
    run_jobs()  # the automatic preview job
    assert analyze(client, project, headers, asset["id"], model=model).status_code == 202
    run_jobs()
    return asset


def snapshot() -> dict[str, str]:
    """A fingerprint of every table the report reads. audit_event is deliberately not included."""
    tables = (
        "project",
        "asset",
        "blob",
        "pipeline_run",
        "observation",
        "observation_revision",
        "conflict",
        "conflict_member",
        "link_group",
        "photo_location",
        "map_annotation",
        "job",
        "artifact",
    )
    with get_engine().connect() as conn:
        return {
            t: conn.execute(
                text(
                    f"SELECT coalesce(md5(string_agg(row_to_json(x)::text, '|' ORDER BY row_to_json(x)::text)), '') FROM {t} x"
                )
            ).scalar_one()
            for t in tables
        }


# --- an empty project, unknown ids, access --------------------------------------------------------------------------


def test_an_empty_project_reports_nothing_as_nothing_and_still_states_its_limits(client, project, headers):
    body = report(client, project, headers)
    assert body["read_only"] is True and body["schema_version"] == "1.1"
    assert body["project"]["id"] == project["id"] and body["project"]["name"] == project["name"]
    assert body["generated_by"] == "Test Reviewer" and body["generated_at"]
    for section in ("assets", "runs", "observations", "photo_locations", "conflicts"):
        assert body[section] == [], section
    summary = body["summary"]
    assert summary["observations_total"] == 0 and summary["assets_total"] == 0 and summary["photos_total"] == 0
    assert summary["observations_by_review_status"] == {
        "proposed": 0,
        "needs_review": 0,
        "verified": 0,
        "inconclusive": 0,
        "disputed": 0,
        "rejected": 0,
    }
    assert body["annotations"]["available"] is True and body["annotations"]["items"] == []
    assert (summary["annotations_active"], summary["annotations_deleted"]) == (0, 0)
    assert body["counting_rules"] and body["limitations"]
    joined = " ".join(body["limitations"])
    assert "not a probability" in joined and "not the precise location of any object" in joined
    assert "require human review" in joined and "unavailable" in joined


def test_an_unknown_project_is_a_404_problem_and_is_not_audited(client, headers):
    response = client.get("/api/v1/projects/00000000-0000-4000-8000-000000000001/report", headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == "PROJECT_NOT_FOUND"
    assert rows("SELECT count(*) FROM audit_event WHERE action = 'report.generate'")[0][0] == 0


def test_a_malformed_project_id_is_a_validation_problem(client, headers):
    assert client.get("/api/v1/projects/not-a-uuid/report", headers=headers).status_code == 422


def test_a_report_needs_a_local_profile(client, project):
    response = client.get(f"/api/v1/projects/{project['id']}/report")
    assert response.status_code == 401
    assert rows("SELECT count(*) FROM audit_event WHERE action = 'report.generate'")[0][0] == 0


# --- reviewed and unreviewed findings, notes, provenance -------------------------------------------------------------


def test_reviewed_and_unreviewed_findings_notes_and_provenance(client, project, headers, run_jobs):
    asset = analysed_photo(client, project, headers, run_jobs)
    items = listing(client, project)
    a, z = by_category(items, "test:thing a"), by_category(items, "test:thing b")
    assert (
        act(
            client, headers, a["observation"]["observation_id"], "confirm", 0, note="Checked against the print"
        ).status_code
        == 200
    )
    assert act(client, headers, z["observation"]["observation_id"], "note", 0, note=HOSTILE).status_code == 200

    body = report(client, project, headers)
    summary = body["summary"]
    assert summary["observations_total"] == 2
    assert summary["observations_reviewed"] == 1 and summary["observations_unreviewed"] == 1
    assert summary["observations_by_review_status"]["verified"] == 1
    assert summary["observations_by_review_status"]["needs_review"] == 1
    assert summary["observations_with_reviewer_notes"] == 2  # a note on a finding does not make it reviewed
    assert summary["observations_located"] == 0 and summary["observations_unlocated"] == 2

    by_id = {o["observation_id"]: o for o in body["observations"]}
    verified = by_id[a["observation"]["observation_id"]]
    assert (verified["review_status"], verified["review_group"]) == ("verified", "reviewed")
    assert verified["model_category"] == verified["current_category"] == "test:thing a"
    assert verified["model"] == {
        "name": STUB["name"],
        "version": STUB["version"],
        "run_id": verified["model"]["run_id"],
    }
    assert verified["score_value"] == a["observation"]["score"]["raw_value"]
    assert verified["score_calibrated"] is False and verified["score_semantics"]
    assert verified["asset_id"] == asset["id"] and verified["asset_filename"] == "photo.jpg"
    (region,) = [e["region"] for e in verified["evidence"] if e["region"]]
    assert region["coordinate_space"] == "source_image_pixels" and region["type"] == "bbox_xyxy"
    assert verified["geographically_located"] is False
    (note,) = verified["reviewer_notes"]
    assert note["note"] == "Checked against the print" and note["actor_label"] == "Test Reviewer"
    assert note["actor_kind"] == "reviewer" and (note["status_before"], note["status_after"]) == (
        "needs_review",
        "verified",
    )
    assert [r["revision_no"] for r in verified["revisions"]] == [1]

    unreviewed = by_id[z["observation"]["observation_id"]]
    assert (unreviewed["review_status"], unreviewed["review_group"]) == ("needs_review", "unreviewed")
    assert unreviewed["reviewer_notes"][0]["note"] == HOSTILE  # stored text is returned exactly, never altered

    (run,) = body["runs"]
    assert (run["model_name"], run["model_version"]) == (STUB["name"], STUB["version"])
    assert run["asset_id"] == asset["id"] and run["observation_count"] == 2
    (listed,) = body["assets"]
    assert listed["sha256"] == asset["sha256"] and listed["filename"] == "photo.jpg"
    assert listed["media_type"] == asset["media_type"] and listed["observation_count"] == 2
    assert (
        summary["scores_not_calibrated"] == 2
        and summary["scores_calibrated"] == 0
        and summary["scores_unavailable"] == 0
    )


def test_missing_run_provenance_is_null_and_named_not_invented(client, project, headers, run_jobs):
    analysed_photo(client, project, headers, run_jobs)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE pipeline_run SET weights_sha256 = NULL, device = NULL"))
    (run,) = report(client, project, headers)["runs"]
    assert run["weights_sha256"] is None and run["device"] is None
    assert run["unavailable_fields"] == ["weights_sha256", "device"]
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE pipeline_run SET weights_sha256 = :w, device = 'cpu'"), {"w": "a" * 64})
    (run,) = report(client, project, headers)["runs"]
    assert run["weights_sha256"] == "a" * 64 and run["device"] == "cpu" and run["unavailable_fields"] == []


# --- photograph locations -------------------------------------------------------------------------------------------


def test_only_person_confirmed_positions_are_locations_and_candidates_show_no_coordinates(client, project, headers):
    candidate, _, _ = photo_with(client, project, headers, name="candidate.jpg", seed=1)
    confirmed, _, _ = photo_with(client, project, headers, name="confirmed.jpg", seed=2)
    plain = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG", seed=3)).json()["asset"]
    manual = upload(client, project, headers, "manual.jpg", media.image_bytes("JPEG", seed=4)).json()["asset"]
    assert (
        change(
            client, headers, confirmed["id"], action="confirm_exif", expected_version=1, reason="Checked on site"
        ).status_code
        == 201
    )
    placed = change(
        client,
        headers,
        manual["id"],
        action="set_position",
        method="manual_entry",
        latitude=12.5,
        longitude=45.25,
        expected_version=0,
    )
    assert placed.status_code == 201, placed.text

    response = client.get(f"/api/v1/projects/{project['id']}/report", headers=headers)
    body = response.json()
    summary = body["summary"]
    assert (summary["photos_total"], summary["photos_confirmed_location"]) == (4, 2)
    assert (summary["photos_unconfirmed_candidate"], summary["photos_without_location"]) == (1, 1)

    photos = {p["filename"]: p for p in body["photo_locations"]}
    assert photos["candidate.jpg"]["state"] == "unconfirmed_candidate" and photos["candidate.jpg"]["confirmed"] is None
    assert photos["plain.jpg"]["state"] == "none" and photos["plain.jpg"]["confirmed"] is None
    # No candidate coordinate appears anywhere for the unconfirmed photograph.
    candidate_text = json.dumps(photos["candidate.jpg"])
    assert "28.6" not in candidate_text and "77.2" not in candidate_text
    (step,) = photos["candidate.jpg"]["history"]
    assert (step["status"], step["coordinates_withheld"], step["latitude"], step["longitude"]) == (
        "candidate",
        True,
        None,
        None,
    )

    c = photos["confirmed.jpg"]
    assert c["state"] == "confirmed"
    assert (c["confirmed"]["latitude"], c["confirmed"]["longitude"], c["confirmed"]["crs_id"]) == (
        28.6,
        77.2,
        "EPSG:4326",
    )
    assert c["confirmed"]["source"] == "exif" and c["confirmed"]["confirmed_by"] == "Test Reviewer"
    assert c["confirmed"]["reason"] == "Checked on site"
    assert [(s["status"], s["coordinates_withheld"]) for s in c["history"]] == [
        ("candidate", True),
        ("confirmed", False),
    ]
    assert c["history"][0]["latitude"] is None  # the EXIF candidate row never shows its numbers, even once confirmed
    assert c["history"][1]["candidate_version_no"] == 1

    m = photos["manual.jpg"]
    assert (m["confirmed"]["latitude"], m["confirmed"]["longitude"], m["confirmed"]["source"]) == (
        12.5,
        45.25,
        "manual_entry",
    )
    assert plain["id"] and candidate["id"]


def test_clearing_a_confirmed_location_removes_it_from_the_report(client, project, headers):
    asset, _, _ = photo_with(client, project, headers)
    assert change(client, headers, asset["id"], action="confirm_exif", expected_version=1).status_code == 201
    assert (
        change(client, headers, asset["id"], action="clear", expected_version=2, reason="Wrong place").status_code
        == 201
    )
    body = report(client, project, headers)
    (entry,) = body["photo_locations"]
    assert entry["state"] == "none" and entry["confirmed"] is None
    assert body["summary"]["photos_confirmed_location"] == 0 and body["summary"]["photos_without_location"] == 1
    assert [s["status"] for s in entry["history"]] == ["candidate", "confirmed", "cleared"]


# --- conflicts ------------------------------------------------------------------------------------------------------


def test_conflicts_are_listed_with_their_stored_status(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    analyze(client, project, headers, asset["id"])
    run_jobs()
    analyze(client, project, headers, asset["id"], model=ALT)
    run_jobs()
    body = report(client, project, headers)
    (conflict,) = body["conflicts"]
    assert conflict["status"] == "open" and conflict["attribute"] == "category"
    assert sorted(m["value"]["category"] for m in conflict["members"]) == ["test:thing a", "test:thing z"]
    assert body["summary"]["conflicts_open"] == 1 and body["summary"]["conflicts_resolved"] == 0
    assert body["summary"]["observations_by_review_status"]["disputed"] == 2
    assert body["summary"]["observations_unreviewed"] == 4  # disputed is not reviewed
    assert len(body["runs"]) == 2 and {r["model_name"] for r in body["runs"]} == {STUB["name"], ALT["name"]}
    assert any(o["open_conflict_ids"] == [conflict["id"]] for o in body["observations"])


# --- isolation ------------------------------------------------------------------------------------------------------


def test_a_report_contains_nothing_from_another_project(client, project, headers, run_jobs):
    analysed_photo(client, project, headers, run_jobs, name="mine.jpg")
    other = new_project(client, headers, "Other project")
    theirs = analysed_photo(client, other, headers, run_jobs, name="theirs.jpg", seed=7)
    other_photo, _, _ = photo_with(client, other, headers, name="theirs-gps.jpg", seed=9)
    change(client, headers, other_photo["id"], action="confirm_exif", expected_version=1)
    # Make the other project's findings disagree so it has a conflict of its own.
    analyze(client, other, headers, theirs["id"], model=ALT)
    run_jobs()

    mine = report(client, project, headers)
    text_ = json.dumps(mine)
    for needle in (
        "theirs.jpg",
        "theirs-gps.jpg",
        "Other project",
        theirs["id"],
        theirs["sha256"],
        other["id"],
        other_photo["id"],
    ):
        assert needle not in text_, f"{needle} leaked into another project's report"
    assert mine["summary"]["observations_total"] == 2 and mine["summary"]["assets_total"] == 1
    assert mine["conflicts"] == [] and len(mine["runs"]) == 1 and mine["photo_locations"][0]["filename"] == "mine.jpg"
    assert mine["summary"]["photos_confirmed_location"] == 0

    theirs_report = report(client, other, headers)
    assert "mine.jpg" not in json.dumps(theirs_report)
    assert theirs_report["summary"]["photos_confirmed_location"] == 1 and len(theirs_report["conflicts"]) == 1


# --- untrusted text -------------------------------------------------------------------------------------------------


def test_untrusted_text_is_returned_as_data_in_a_json_response(client, headers):
    project = new_project(client, headers, HOSTILE)
    response = client.get(f"/api/v1/projects/{project['id']}/report", headers=headers)
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["project"]["name"] == HOSTILE
    # json.dumps escapes quotes and backslashes; nothing is HTML-interpreted by the API.
    assert json.loads(response.text)["project"]["name"] == HOSTILE


def test_a_hostile_filename_is_carried_as_the_text_that_was_stored(client, project, headers):
    stored = upload(client, project, headers, '"><img src=x onerror=alert(1)>.jpg', media.image_bytes("JPEG")).json()[
        "asset"
    ]
    body = report(client, project, headers)
    assert [a["filename"] for a in body["assets"]] == [stored["original_filename"]]
    assert "onerror" in body["assets"][0]["filename"]  # kept as text; the page must escape it when it renders


# --- read-only and audited -------------------------------------------------------------------------------------------


def test_generating_a_report_changes_no_stored_record_and_adds_one_audit_event(client, project, headers, run_jobs):
    asset = analysed_photo(client, project, headers, run_jobs)
    other, _, _ = photo_with(client, project, headers, name="gps.jpg", seed=5)
    change(client, headers, other["id"], action="confirm_exif", expected_version=1)
    first = listing(client, project)[0]
    act(client, headers, first["observation"]["observation_id"], "note", 0, note="a note")
    before = snapshot()
    audits_before = rows("SELECT count(*) FROM audit_event")[0][0]

    for _ in range(2):
        assert client.get(f"/api/v1/projects/{project['id']}/report", headers=headers).status_code == 200

    assert snapshot() == before, "generating a report changed stored records"
    assert rows("SELECT count(*) FROM audit_event")[0][0] == audits_before + 2
    events = rows("SELECT actor_id, entity_type, entity_id, details FROM audit_event WHERE action = 'report.generate'")
    assert len(events) == 2
    actor_id, entity_type, entity_id, details = events[0]
    assert str(entity_id) == project["id"] and entity_type == "project"
    assert details["format"] == "json" and details["observations"] == 2 and details["assets"] == 2
    assert details["photos_with_confirmed_location"] == 1
    assert asset["id"]


def test_the_report_is_documented_in_the_openapi_schema(client):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/projects/{project_id}/report"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/FindingsReport")
    assert {"401", "404", "422"} <= set(operation["responses"])
