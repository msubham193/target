"""Map annotations in the findings report and the JSON and GeoJSON exports.

What is checked: annotations have their own section/member, never appear among observations or in the CSV; active and
deleted annotations are always distinguishable and a deleted one is never a GeoJSON feature; every export and the report
are read-only (the append-only table is not touched) and limited to one project; they need a local profile; hostile text
comes back exactly as written and is never interpreted; a database without the annotation table is described, not guessed.

Scratch database geopoc_test and scratch data folder only (see conftest.py).
"""

import csv
import io
import json
from datetime import datetime

import pytest

from geopoc.geo import annotations as annotation_service
from tests.test_annotations import LINE, POINT, SQUARE, base, created, delete, edit, other_project
from tests.test_assets import rows
from tests.test_findings_report import analysed_photo, report, snapshot

HOSTILE = '<script>alert("x")</script> & <img src=x onerror=alert(1)> </script> "quoted" \\ ‮日本語\n\ttab {{7*7}} =1+1'
MOVED = {"type": "LineString", "coordinates": [[75.0, 27.0], [75.02, 27.0], [75.02, 27.01]]}


def url(project, what: str) -> str:
    return f"/api/v1/projects/{project['id']}/{what}"


def geojson(client, project, headers, **params):
    response = client.get(url(project, "exports/observations.geojson"), headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def full_json(client, project, headers, **params):
    response = client.get(url(project, "exports/observations.json"), headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def annotation_features(document):
    return [f for f in document["features"] if f["properties"].get("feature_type") == "user_annotation"]


def table_fingerprint() -> list:
    return rows(
        "SELECT annotation_id, version_no, status, note, md5(ST_AsEWKB(geom)::text), actor_label, created_at "
        "FROM map_annotation ORDER BY annotation_id, version_no"
    )


@pytest.fixture
def story(client, project, headers):
    """One annotation of each kind: a point left alone, a line edited twice (note, then shape), a polygon deleted."""
    point = created(client, project, headers, kind="point", note="gate")
    line = created(client, project, headers, kind="line", note="fence")
    line2 = edit(client, project, headers, line, note="fence, east side", reason="clarified").json()
    line3 = edit(client, project, headers, line2, geometry=MOVED).json()
    polygon = created(client, project, headers, kind="polygon", note="field")
    gone = delete(client, project, headers, polygon, reason="drawn in error").json()
    return {"point": point, "line": line3, "polygon": gone}


# --- the findings report ---------------------------------------------------------------------------------------------


def test_the_report_lists_annotations_in_their_own_section_with_status_geometry_note_author_version_and_times(
    client, project, headers, story
):
    body = report(client, project, headers)
    section = body["annotations"]
    assert section["available"] is True and (section["active"], section["deleted"]) == (2, 1)
    assert "not authenticated" in section["note"] and "not a finding" in section["note"]
    by_note = {i["note"]: i for i in section["items"]}
    assert set(by_note) == {"gate", "fence, east side", "field"}

    gate = by_note["gate"]
    assert gate["feature_type"] == "user_annotation" and gate["annotation_id"] == story["point"]["annotation_id"]
    assert (gate["kind"], gate["status"], gate["version_no"], gate["crs_id"]) == ("point", "active", 1, "EPSG:4326")
    assert gate["geometry"] == POINT and gate["measurement"] is None
    assert gate["created_by"] == gate["updated_by"] == "Test Reviewer"
    assert gate["created_at"] and gate["updated_at"] and gate["deleted_by"] is None and gate["deleted_at"] is None

    fence = by_note["fence, east side"]
    assert (fence["kind"], fence["status"], fence["version_no"], fence["geometry"]) == ("line", "active", 3, MOVED)
    assert fence["measurement"]["length_m"] > 0 and fence["measurement"]["limitations"]
    assert [(v["version_no"], v["action"], v["geometry_changed"]) for v in fence["versions"]] == [
        (1, "create", True),
        (2, "update", False),  # only the note changed
        (3, "update", True),  # the shape moved
    ]
    assert [v["note"] for v in fence["versions"]] == ["fence", "fence, east side", "fence, east side"]
    assert fence["versions"][1]["reason"] == "clarified"

    field = by_note["field"]
    assert (field["kind"], field["status"], field["version_no"]) == ("polygon", "deleted", 2)
    assert field["geometry"] == SQUARE  # the last shape, shown as deleted, never as an active mark
    assert field["measurement"] is None  # a deleted annotation is not measured
    assert field["deleted_by"] == "Test Reviewer" and field["deleted_at"]
    assert [(v["action"], v["status"], v["geometry_changed"]) for v in field["versions"]] == [
        ("create", "active", True),
        ("delete", "deleted", False),
    ]
    assert field["versions"][1]["reason"] == "drawn in error"


def test_annotations_are_counted_apart_from_findings_and_photographs(client, project, headers, run_jobs, story):
    analysed_photo(client, project, headers, run_jobs)
    body = report(client, project, headers)
    summary = body["summary"]
    assert (summary["annotations_active"], summary["annotations_deleted"]) == (2, 1)
    assert summary["observations_total"] == len(body["observations"]) > 0
    assert summary["photos_total"] == 1 and summary["assets_total"] == 1
    assert all("annotation" not in json.dumps(o).lower() for o in body["observations"])
    assert any("Map annotations" in rule for rule in body["counting_rules"])
    assert any("map annotation" in text.lower() and "not authenticated" in text for text in body["limitations"])


def test_a_project_with_no_annotations_says_so(client, project, headers):
    section = report(client, project, headers)["annotations"]
    assert section["available"] is True and section["items"] == []
    assert (section["active"], section["deleted"]) == (0, 0)


def test_a_project_whose_annotations_are_all_deleted_lists_none_as_active(client, project, headers):
    item = created(client, project, headers, note="only one")
    assert delete(client, project, headers, item).status_code == 200
    section = report(client, project, headers)["annotations"]
    assert (section["active"], section["deleted"]) == (0, 1)
    assert [i["status"] for i in section["items"]] == ["deleted"]
    assert not annotation_features(geojson(client, project, headers))


def test_generating_a_report_or_an_export_never_changes_the_append_only_history(client, project, headers, story):
    before, before_snapshot = table_fingerprint(), snapshot()
    report(client, project, headers)
    geojson(client, project, headers)
    full_json(client, project, headers)
    assert client.get(url(project, "exports/observations.csv"), headers=headers).status_code == 200
    assert table_fingerprint() == before
    assert snapshot() == before_snapshot
    assert rows("SELECT count(*) FROM map_annotation")[0][0] == len(before) == 6  # 1 + 3 + 2 versions


def test_the_report_records_who_generated_it_and_how_many_annotations_were_in_it(client, project, headers, story):
    report(client, project, headers)
    (details,) = rows("SELECT details FROM audit_event WHERE action = 'report.generate'")[0]
    assert (details["annotations_active"], details["annotations_deleted"]) == (2, 1)
    assert "gate" not in json.dumps(details)  # note text is not copied into the audit log


# --- the JSON export -------------------------------------------------------------------------------------------------


def test_the_json_export_has_an_annotations_member_with_active_and_deleted_items_and_every_version(
    client, project, headers, story
):
    document = full_json(client, project, headers)
    section = document["annotations"]
    assert section["available"] is True and (section["active"], section["deleted"]) == (2, 1)
    assert [i["status"] for i in section["items"]] == ["active", "active", "deleted"]  # oldest first
    by_note = {i["note"]: i for i in section["items"]}
    fence = by_note["fence, east side"]
    assert fence["annotation_id"] == story["line"]["annotation_id"] and fence["kind"] == "line"
    assert fence["geometry"] == MOVED and fence["created_by"] == "Test Reviewer" and fence["version_no"] == 3
    assert [v["version_no"] for v in fence["versions"]] == [1, 2, 3]
    assert fence["versions"][0]["geometry"] == LINE and fence["versions"][2]["geometry"] == MOVED
    assert all(v["actor_label"] == "Test Reviewer" and v["created_at"] for v in fence["versions"])
    field = by_note["field"]
    assert field["status"] == "deleted" and field["deleted_by"] == "Test Reviewer" and field["deleted_at"]
    assert [v["status"] for v in field["versions"]] == ["active", "deleted"]
    # never mixed into the observation part of the file
    assert document["observations"] == [] and "annotation" not in json.dumps(document["photo_locations"])


def test_the_observation_status_filter_does_not_hide_annotations(client, project, headers, story):
    document = full_json(client, project, headers, status="verified")
    assert document["filter"] == {"status": ["verified"]}
    assert len(document["annotations"]["items"]) == 3
    assert len(annotation_features(geojson(client, project, headers, status="verified"))) == 2


def test_the_json_export_without_annotations_has_an_empty_member(client, project, headers):
    section = full_json(client, project, headers)["annotations"]
    assert section["available"] is True and section["items"] == [] and (section["active"], section["deleted"]) == (0, 0)


# --- the GeoJSON export ----------------------------------------------------------------------------------------------


def test_the_geojson_export_has_active_annotations_as_separate_features_and_counts_the_deleted(
    client, project, headers, story
):
    document = geojson(client, project, headers)
    features = annotation_features(document)
    assert len(features) == 2 and len(document["features"]) == 2  # nothing else in this project
    by_note = {f["properties"]["note"]: f for f in features}
    assert set(by_note) == {"gate", "fence, east side"}  # "field" was deleted

    gate = by_note["gate"]
    assert gate["type"] == "Feature" and gate["id"] == f"annotation:{story['point']['annotation_id']}"
    assert gate["geometry"] == POINT
    properties = dict(gate["properties"])
    stamps = (properties.pop("created_at"), properties.pop("updated_at"))
    assert properties == {
        "feature_type": "user_annotation",
        "annotation_id": story["point"]["annotation_id"],
        "kind": "point",
        "status": "active",
        "note": "gate",
        "version_no": 1,
        "crs_id": "EPSG:4326",
        "created_by": "Test Reviewer",
        "updated_by": "Test Reviewer",
    }
    assert all(datetime.fromisoformat(stamp).tzinfo is not None for stamp in stamps)  # real timestamps, with a zone
    fence = by_note["fence, east side"]
    assert (
        fence["geometry"] == MOVED and fence["properties"]["kind"] == "line" and fence["properties"]["version_no"] == 3
    )

    summary = document["annotations"]
    assert (summary["available"], summary["exported"], summary["deleted_not_exported"]) == (True, 2, 1)
    assert "not drawn or exported as a feature" in summary["reason"]
    deleted_id = story["polygon"]["annotation_id"]
    assert deleted_id not in json.dumps(document["features"])  # not a feature in any form
    assert document["excluded"]["count"] == 0  # `excluded` still describes observations only


def test_geojson_annotation_features_are_never_marked_active_when_deleted(client, project, headers, story):
    statuses = {f["properties"]["status"] for f in annotation_features(geojson(client, project, headers))}
    assert statuses == {"active"}


def test_geojson_keeps_observation_and_photograph_features_unchanged_beside_annotations(
    client, project, headers, run_jobs
):
    analysed_photo(client, project, headers, run_jobs)
    before = geojson(client, project, headers)
    created(client, project, headers, note="mark")
    after = geojson(client, project, headers)
    non_annotation = [f for f in after["features"] if f["properties"].get("feature_type") != "user_annotation"]
    assert non_annotation == before["features"]
    for key in ("excluded", "photographs", "coordinate_reference"):
        assert after[key] == before[key]
    assert len(annotation_features(after)) == 1


# --- the CSV export is unchanged -------------------------------------------------------------------------------------


def test_the_csv_export_is_identical_with_and_without_annotations(client, project, headers, run_jobs):
    other = other_project(client, headers, "csv twin")
    analysed_photo(client, project, headers, run_jobs)
    csv_before = client.get(url(project, "exports/observations.csv"), headers=headers).content
    for kind in ("point", "line", "polygon"):
        created(client, project, headers, kind=kind, note="more")
    created(client, other, headers, note="elsewhere")
    csv_after = client.get(url(project, "exports/observations.csv"), headers=headers).content
    assert csv_after == csv_before
    table = list(csv.reader(io.StringIO(csv_after.decode("utf-8-sig"))))
    assert len(table) > 1 and all(len(r) == len(table[0]) for r in table)
    assert b"annotation" not in csv_after.lower() and b"more" not in csv_after and b"gate" not in csv_after
    assert not any("annotation" in h.lower() for h in table[0])


def test_the_csv_audit_record_keeps_its_old_shape_while_json_and_geojson_add_annotation_counts(
    client, project, headers, story
):
    for what in ("csv", "geojson", "json"):
        assert client.get(url(project, f"exports/observations.{what}"), headers=headers).status_code == 200
    details = {
        fmt: d
        for fmt, d in rows("SELECT details ->> 'format', details FROM audit_event WHERE action = 'export.observations'")
    }
    assert set(details["csv"]) == {"format", "records"}
    assert details["json"]["annotations_active"] == 2 and details["json"]["annotations_deleted"] == 1
    assert (
        details["geojson"]["annotations_exported"] == 2 and details["geojson"]["annotations_deleted_not_exported"] == 1
    )
    assert "gate" not in json.dumps(details)


# --- project isolation -----------------------------------------------------------------------------------------------


def test_nothing_from_another_project_appears_in_the_report_or_either_export(client, project, headers, story):
    other = other_project(client, headers, "Somewhere else")
    theirs = created(client, other, headers, kind="line", note="ZZ-OTHER-PROJECT")
    gone = created(client, other, headers, kind="point", note="ZZ-OTHER-DELETED")
    delete(client, other, headers, gone)

    mine = {
        "report": client.get(url(project, "report"), headers=headers).text,
        "json": client.get(url(project, "exports/observations.json"), headers=headers).text,
        "geojson": client.get(url(project, "exports/observations.geojson"), headers=headers).text,
    }
    for name, text in mine.items():
        assert "ZZ-OTHER" not in text and theirs["annotation_id"] not in text, name
        assert story["point"]["annotation_id"] in text, name
    assert report(client, project, headers)["summary"]["annotations_deleted"] == 1  # mine only, not the other's

    body = report(client, other, headers)
    assert [i["note"] for i in body["annotations"]["items"]] == ["ZZ-OTHER-PROJECT", "ZZ-OTHER-DELETED"]
    assert (body["annotations"]["active"], body["annotations"]["deleted"]) == (1, 1)
    assert "gate" not in client.get(url(other, "exports/observations.json"), headers=headers).text
    assert "gate" not in client.get(url(other, "exports/observations.geojson"), headers=headers).text


def test_an_unknown_project_gives_404_for_the_report_and_both_exports(client, headers):
    missing = "00000000-0000-4000-8000-000000000001"
    for path in ("report", "exports/observations.json", "exports/observations.geojson"):
        response = client.get(f"/api/v1/projects/{missing}/{path}", headers=headers)
        assert response.status_code == 404 and response.json()["code"] == "PROJECT_NOT_FOUND", path


# --- profiles and authorization --------------------------------------------------------------------------------------


def test_the_report_and_the_exports_still_need_a_local_profile_and_leak_no_annotation_without_one(
    client, project, story
):
    for path in ("report", "exports/observations.json", "exports/observations.geojson", "exports/observations.csv"):
        response = client.get(url(project, path))
        assert response.status_code == 401, path
        assert "gate" not in response.text and "annotation_id" not in response.text, path
    assert (
        rows("SELECT count(*) FROM audit_event WHERE action IN ('report.generate', 'export.observations')")[0][0] == 0
    )


def test_an_unknown_profile_is_refused_too(client, project, story):
    response = client.get(
        url(project, "report"), headers={"X-Workspace-Profile": "00000000-0000-4000-8000-0000000000aa"}
    )
    assert response.status_code == 401


# --- hostile text ----------------------------------------------------------------------------------------------------


def test_hostile_note_text_comes_back_exactly_as_written_everywhere_and_is_never_served_as_a_page(
    client, project, headers
):
    item = created(client, project, headers, kind="line", note=HOSTILE)
    edit(client, project, headers, item, note=HOSTILE + " v2", reason=HOSTILE)

    body = report(client, project, headers)
    (annotation,) = body["annotations"]["items"]
    assert annotation["note"] == HOSTILE + " v2"
    assert [v["note"] for v in annotation["versions"]] == [HOSTILE, HOSTILE + " v2"]
    assert annotation["versions"][1]["reason"] == HOSTILE

    json_response = client.get(url(project, "exports/observations.json"), headers=headers)
    geo_response = client.get(url(project, "exports/observations.geojson"), headers=headers)
    exported = json_response.json()["annotations"]["items"][0]
    assert exported["note"] == HOSTILE + " v2" and exported["versions"][0]["note"] == HOSTILE
    assert annotation_features(geo_response.json())[0]["properties"]["note"] == HOSTILE + " v2"

    for response in (json_response, geo_response):
        assert response.headers["content-type"].split(";")[0] in {"application/json", "application/geo+json"}
        assert response.headers["content-disposition"].startswith("attachment;")
    assert client.get(url(project, "report"), headers=headers).headers["content-type"].startswith("application/json")


def test_a_long_note_and_a_note_that_looks_like_markup_or_a_spreadsheet_formula_are_data_only(client, project, headers):
    notes = ["x" * 2000, '=HYPERLINK("http://example.invalid")', "@SUM(1+1)", "<b>bold</b>", "\\u0000", "' OR 1=1 --"]
    for note in notes:
        created(client, project, headers, note=note)
    exported = [i["note"] for i in full_json(client, project, headers)["annotations"]["items"]]
    assert exported == notes
    assert [i["note"] for i in report(client, project, headers)["annotations"]["items"]] == notes
    assert len(annotation_features(geojson(client, project, headers))) == len(notes)


# --- a database that has not been updated for annotations -------------------------------------------------------------


def test_a_database_without_the_annotation_table_is_described_not_guessed(client, project, headers, monkeypatch):
    monkeypatch.setattr(annotation_service, "table_exists", lambda _session: False)

    body = report(client, project, headers)
    section = body["annotations"]
    assert section["available"] is False and section["items"] == []
    assert section["active"] is None and section["deleted"] is None  # unknown, not zero
    assert "does not mean the project has none" in section["note"]
    assert body["summary"]["annotations_active"] is None and body["summary"]["annotations_deleted"] is None

    document = full_json(client, project, headers)["annotations"]
    assert document["available"] is False and document["items"] == [] and document["active"] is None
    geo = geojson(client, project, headers)
    assert annotation_features(geo) == []
    assert geo["annotations"]["available"] is False and geo["annotations"]["deleted_not_exported"] is None
    assert client.get(url(project, "exports/observations.csv"), headers=headers).status_code == 200


# --- the annotation endpoints themselves are as they were ------------------------------------------------------------


def test_the_annotation_listing_still_agrees_with_the_report(client, project, headers, story):
    listing = client.get(base(project), params={"include_deleted": "true"}).json()
    section = report(client, project, headers)["annotations"]
    assert [i["annotation_id"] for i in listing["items"]] == [i["annotation_id"] for i in section["items"]]
    assert [i["status"] for i in listing["items"]] == [i["status"] for i in section["items"]]
    assert (listing["active"], listing["deleted"]) == (section["active"], section["deleted"])
