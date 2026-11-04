"""Map annotations: points, lines and polygons a person draws, with a note.

What is checked: they persist, are validated (geometry and coordinate system), are measured with units and limits, keep
their history, are hidden but not removed when deleted, never leak between projects, never appear among AI observations,
photograph locations or CSV rows, and every change is audited. Their place in the findings report and the JSON and
GeoJSON exports is tested in test_annotation_reporting.py.

Scratch database geopoc_test and scratch data folder only (see conftest.py).
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from geopoc.api.errors import ProblemError
from geopoc.api.main import app
from geopoc.api.routers.annotations import annotations_available
from geopoc.db.session import get_engine
from tests import media
from tests.test_assets import rows, upload
from tests.test_ground_pipeline import analyze
from tests.test_photo_locations import change, photo_with

UNKNOWN_UUID = "00000000-0000-4000-8000-00000000dead"

POINT = {"type": "Point", "coordinates": [75.0, 27.0]}
LINE = {"type": "LineString", "coordinates": [[75.0, 27.0], [75.01, 27.0]]}
SQUARE = {
    "type": "Polygon",
    "coordinates": [[[75.0, 27.0], [75.001, 27.0], [75.001, 27.001], [75.0, 27.001], [75.0, 27.0]]],
}
BOWTIE = {
    "type": "Polygon",
    "coordinates": [[[75.0, 27.0], [75.002, 27.002], [75.002, 27.0], [75.0, 27.002], [75.0, 27.0]]],
}


def base(project) -> str:
    return f"/api/v1/projects/{project['id']}/annotations"


def make(client, project, headers, kind="point", geometry=None, note="a note", **extra):
    geometry = geometry or {"point": POINT, "line": LINE, "polygon": SQUARE}[kind]
    return client.post(base(project), json={"kind": kind, "geometry": geometry, "note": note, **extra}, headers=headers)


def created(client, project, headers, **kwargs) -> dict:
    response = make(client, project, headers, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def other_project(client, headers, name="Other"):
    response = client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def table_rows():
    return rows("SELECT count(*) FROM map_annotation")[0][0]


def counts():
    with get_engine().connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in ("observation", "observation_revision", "photo_location", "link_group", "conflict", "asset", "job")
        }


# --- create, read, persist ------------------------------------------------------------------------------------------


def test_points_lines_and_polygons_are_saved_listed_and_kept_after_a_reload(client, project, headers):
    point = created(client, project, headers, kind="point", note="gate")
    line = created(client, project, headers, kind="line", note="fence")
    polygon = created(client, project, headers, kind="polygon", note="field")

    for item, kind in ((point, "point"), (line, "line"), (polygon, "polygon")):
        assert item["feature_type"] == "user_annotation"
        assert item["kind"] == kind and item["status"] == "active" and item["version_no"] == 1
        assert item["crs_id"] == "EPSG:4326" and item["project_id"] == project["id"]
        assert item["created_by"] == "Test Reviewer" == item["updated_by"]
        assert item["deleted_by"] is None

    # a new application instance (a reload) sees the same annotations
    with TestClient(app) as fresh:
        listing = fresh.get(base(project)).json()
    assert [i["note"] for i in listing["items"]] == ["gate", "fence", "field"]
    assert [i["geometry"] for i in listing["items"]] == [POINT, LINE, SQUARE]
    assert (listing["active"], listing["deleted"], listing["include_deleted"]) == (3, 0, False)
    assert "not authenticated" in listing["note"] and "not a finding" in listing["note"]


def test_the_note_is_stored_exactly_as_written_and_never_interpreted(client, project, headers):
    text_ = '<img src=x onerror="alert(1)"> & "quoted" — 日本語\nsecond line'
    item = created(client, project, headers, note=text_)
    assert client.get(f"{base(project)}/{item['annotation_id']}").json()["note"] == text_


def test_an_annotation_can_have_an_empty_note(client, project, headers):
    assert created(client, project, headers, note="")["note"] == ""


def test_a_project_with_no_annotations_has_an_empty_list(client, project):
    body = client.get(base(project)).json()
    assert body["items"] == [] and (body["active"], body["deleted"]) == (0, 0)


def test_creating_requires_a_profile_and_attributes_the_change_to_it_only(client, project, headers):
    assert client.post(base(project), json={"kind": "point", "geometry": POINT}).status_code == 401
    assert table_rows() == 0
    other = client.post("/api/v1/workspace/profiles", json={"display_name": "Second Person"}).json()
    item = created(client, project, {"X-Workspace-Profile": other["id"]})
    assert item["created_by"] == "Second Person"


def test_an_unknown_project_is_a_404(client, headers):
    url = f"/api/v1/projects/{UNKNOWN_UUID}/annotations"
    assert client.get(url).status_code == 404
    response = client.post(url, json={"kind": "point", "geometry": POINT}, headers=headers)
    assert response.status_code == 404 and response.json()["code"] == "PROJECT_NOT_FOUND"


# --- measurement ----------------------------------------------------------------------------------------------------


def test_a_line_has_a_length_and_a_polygon_an_area_with_units_and_stated_limits(client, project, headers):
    point = created(client, project, headers, kind="point")
    line = created(client, project, headers, kind="line")
    polygon = created(client, project, headers, kind="polygon")

    assert point["measurement"] is None  # a point has no length or area

    measured = line["measurement"]
    assert measured["area_m2"] is None and measured["perimeter_m"] is None
    assert 985 < measured["length_m"] < 999  # 0.01 degree of longitude at 27 N is about 992 m
    assert "geodesic" in measured["method"] and "WGS 84" in measured["method"]
    assert any("not a survey measurement" in text_ for text_ in measured["limitations"])

    measured = polygon["measurement"]
    assert measured["length_m"] is None
    assert 10_700 < measured["area_m2"] < 11_300  # about 110.7 m x 99.2 m
    assert 400 < measured["perimeter_m"] < 440
    assert any("outline only" in text_ for text_ in measured["limitations"])
    assert any("straight line on screen" in text_ or "straight line" in text_ for text_ in measured["limitations"])


def test_a_polygon_drawn_in_the_opposite_direction_has_the_same_positive_area(client, project, headers):
    clockwise = {"type": "Polygon", "coordinates": [list(reversed(SQUARE["coordinates"][0]))]}
    a = created(client, project, headers, kind="polygon")["measurement"]["area_m2"]
    b = created(client, project, headers, kind="polygon", geometry=clockwise)["measurement"]["area_m2"]
    assert a > 0 and a == pytest.approx(b)


def test_a_deleted_annotation_is_not_measured(client, project, headers):
    item = created(client, project, headers, kind="line")
    gone = client.delete(f"{base(project)}/{item['annotation_id']}", params={"expected_version": 1}, headers=headers)
    assert gone.status_code == 200 and gone.json()["measurement"] is None


# --- validation -----------------------------------------------------------------------------------------------------


def ring(*points):
    return {"type": "Polygon", "coordinates": [[list(p) for p in points]]}


INVALID = [
    # coordinate system
    ("point", POINT, {"crs_id": "EPSG:3857"}, "ANNOTATION_CRS_UNSUPPORTED"),
    ("point", POINT, {"crs_id": "EPSG:32643"}, "ANNOTATION_CRS_UNSUPPORTED"),
    ("point", POINT, {"crs_id": ""}, "ANNOTATION_CRS_UNSUPPORTED"),
    (
        "point",
        {**POINT, "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}}},
        {},
        "ANNOTATION_CRS_UNSUPPORTED",
    ),
    # ranges and numbers
    ("point", {"type": "Point", "coordinates": [181, 27]}, {}, "ANNOTATION_LONGITUDE_RANGE"),
    ("point", {"type": "Point", "coordinates": [-180.5, 27]}, {}, "ANNOTATION_LONGITUDE_RANGE"),
    ("point", {"type": "Point", "coordinates": [75, 91]}, {}, "ANNOTATION_LATITUDE_RANGE"),
    ("point", {"type": "Point", "coordinates": [75, -90.1]}, {}, "ANNOTATION_LATITUDE_RANGE"),
    ("point", {"type": "Point", "coordinates": [27, 175]}, {}, "ANNOTATION_LATITUDE_RANGE"),  # swapped order
    ("point", {"type": "Point", "coordinates": ["75", "27"]}, {}, "ANNOTATION_COORDINATE_INVALID"),
    ("point", {"type": "Point", "coordinates": [True, 27]}, {}, "ANNOTATION_COORDINATE_INVALID"),
    ("point", {"type": "Point", "coordinates": [75]}, {}, "ANNOTATION_COORDINATE_INVALID"),
    ("point", {"type": "Point", "coordinates": [75, 27, 100]}, {}, "ANNOTATION_COORDINATE_INVALID"),  # no elevation
    ("point", {"type": "Point", "coordinates": None}, {}, "ANNOTATION_COORDINATE_INVALID"),
    ("point", {"type": "Point", "coordinates": [0, 0]}, {}, "ANNOTATION_NULL_ISLAND"),
    ("point", {"type": "Point", "coordinates": [0.000000001, -0.000000001]}, {}, "ANNOTATION_NULL_ISLAND"),
    # shape type
    ("line", POINT, {}, "ANNOTATION_KIND_MISMATCH"),
    ("point", LINE, {}, "ANNOTATION_KIND_MISMATCH"),
    ("polygon", LINE, {}, "ANNOTATION_KIND_MISMATCH"),
    ("polygon", {"type": "MultiPolygon", "coordinates": [SQUARE["coordinates"]]}, {}, "ANNOTATION_KIND_MISMATCH"),
    ("line", {"type": "MultiLineString", "coordinates": [LINE["coordinates"]]}, {}, "ANNOTATION_KIND_MISMATCH"),
    ("point", {"type": "GeometryCollection", "geometries": [POINT]}, {}, "ANNOTATION_KIND_MISMATCH"),
    ("point", {"coordinates": [75, 27]}, {}, "ANNOTATION_KIND_MISMATCH"),
    # lines
    ("line", {"type": "LineString", "coordinates": [[75, 27]]}, {}, "ANNOTATION_TOO_FEW_POINTS"),
    ("line", {"type": "LineString", "coordinates": []}, {}, "ANNOTATION_TOO_FEW_POINTS"),
    ("line", {"type": "LineString", "coordinates": [[75, 27], [75, 27]]}, {}, "ANNOTATION_GEOMETRY_INVALID"),
    ("line", {"type": "LineString", "coordinates": "75,27;75.1,27"}, {}, "ANNOTATION_GEOMETRY_INVALID"),
    ("line", {"type": "LineString", "coordinates": [[179, 27], [-179, 27]]}, {}, "ANNOTATION_ANTIMERIDIAN"),
    (
        "line",
        {"type": "LineString", "coordinates": [[75 + i * 1e-5, 27 + (i % 2) * 1e-5] for i in range(1001)]},
        {},
        "ANNOTATION_TOO_MANY_POINTS",
    ),
    # polygons
    ("polygon", ring((75, 27), (75.1, 27), (75, 27.1)), {}, "ANNOTATION_TOO_FEW_POINTS"),
    ("polygon", ring((75, 27), (75.1, 27), (75.1, 27.1), (75, 27.1)), {}, "ANNOTATION_RING_NOT_CLOSED"),
    ("polygon", BOWTIE, {}, "ANNOTATION_GEOMETRY_INVALID"),
    ("polygon", ring((75, 27), (75.1, 27), (75.2, 27), (75, 27)), {}, "ANNOTATION_GEOMETRY_INVALID"),  # no area
    ("polygon", {"type": "Polygon", "coordinates": []}, {}, "ANNOTATION_GEOMETRY_INVALID"),
    (
        "polygon",
        {
            "type": "Polygon",
            "coordinates": [
                [[75, 27], [75.1, 27], [75.1, 27.1], [75, 27.1], [75, 27]],
                [[75.02, 27.02], [75.05, 27.02], [75.05, 27.05], [75.02, 27.05], [75.02, 27.02]],
            ],
        },
        {},
        "ANNOTATION_HOLES_UNSUPPORTED",
    ),
    ("polygon", ring((179, 27), (-179, 27), (-179, 28), (179, 28), (179, 27)), {}, "ANNOTATION_ANTIMERIDIAN"),
]


@pytest.mark.parametrize(("kind", "geometry", "extra", "code"), INVALID)
def test_every_geometry_and_crs_rule_refuses_with_a_specific_code_and_saves_nothing(
    client, project, headers, kind, geometry, extra, code
):
    response = make(client, project, headers, kind=kind, geometry=geometry, **extra)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == code
    assert response.json()["title"]
    assert table_rows() == 0


def test_not_a_number_coordinates_are_refused(client, project, headers):
    body = '{"kind": "point", "geometry": {"type": "Point", "coordinates": [NaN, 27]}, "note": ""}'
    response = client.post(base(project), content=body, headers={**headers, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert table_rows() == 0


def test_the_database_refuses_what_the_service_would_have_refused(client, project, headers):
    """The CHECK constraints are the last line: a bug in the service cannot store an invalid shape."""
    insert = text(
        "INSERT INTO map_annotation (id, annotation_id, project_id, version_no, action, status, kind, geom, actor_label, "
        "created_at) VALUES (gen_random_uuid(), gen_random_uuid(), :p, 1, 'create', 'active', :k, "
        "ST_GeomFromText(:g, 4326), 'x', now())"
    )
    good = {"p": project["id"], "k": "polygon", "g": "POLYGON((75 27, 75.1 27, 75.1 27.1, 75 27.1, 75 27))"}
    bad = [
        {**good, "k": "point"},  # kind does not match the shape
        {**good, "g": "POLYGON((75 27, 75.2 27.2, 75.2 27, 75 27.2, 75 27))"},  # self-intersecting
        {**good, "g": "POLYGON((175 27, 195 27, 195 28, 175 28, 175 27))"},  # longitude out of range
        {**good, "g": "POLYGON((75 87, 75.1 87, 75.1 97, 75 97, 75 87))"},  # latitude out of range
        {**good, "g": "MULTIPOLYGON(((75 27, 75.1 27, 75.1 27.1, 75 27))) "},
        {
            **good,
            "g": "POLYGON((75 27, 75.1 27, 75.1 27.1, 75 27.1, 75 27),(75.02 27.02, 75.05 27.02, 75.05 27.05, 75.02 27.02))",
        },  # a hole
        {**good, "g": "POLYGON Z((75 27 1, 75.1 27 1, 75.1 27.1 1, 75 27.1 1, 75 27 1))"},  # three-dimensional
        {**good, "k": "line", "g": "POINT(75 27)"},
    ]
    for params in bad:
        with get_engine().begin() as conn, pytest.raises(DBAPIError):
            conn.execute(insert, params)
    with get_engine().begin() as conn:
        conn.execute(insert, good)  # the same statement with a valid shape is accepted
    assert table_rows() == 1


def test_the_database_enforces_the_action_and_version_shape(client, project, headers):
    item = created(client, project, headers)
    sql = text(
        "INSERT INTO map_annotation (id, annotation_id, project_id, version_no, action, status, kind, geom, actor_label, "
        "created_at, crs_id) SELECT gen_random_uuid(), annotation_id, project_id, :v, :a, :s, kind, geom, 'x', now(), :c "
        "FROM map_annotation WHERE annotation_id = :id"
    )
    base_params = {"id": item["annotation_id"], "v": 2, "a": "update", "s": "active", "c": "EPSG:4326"}
    for override in (
        {"v": 1},  # duplicate version
        {"a": "create"},  # create only as version 1
        {"a": "delete"},  # delete needs status deleted
        {"s": "deleted"},  # update needs status active
        {"c": "EPSG:3857"},  # coordinate system
        {"a": "frobnicate"},
    ):
        with get_engine().begin() as conn, pytest.raises(DBAPIError):
            conn.execute(sql, {**base_params, **override})
    with get_engine().begin() as conn:
        conn.execute(sql, base_params)
    assert table_rows() == 2


def test_the_table_refers_to_a_project_and_a_profile_and_to_nothing_else(client):
    """No column ties an annotation to a photograph, an observation, a raster or a job."""
    found = rows(
        "SELECT ccu.table_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name "
        "WHERE tc.table_name = 'map_annotation' AND tc.constraint_type = 'FOREIGN KEY'"
    )
    assert sorted(r[0] for r in found) == ["project", "workspace_user"]
    columns = {
        r[0] for r in rows("SELECT column_name FROM information_schema.columns WHERE table_name = 'map_annotation'")
    }
    assert not {c for c in columns if "asset" in c or "observation" in c or "photo" in c or "job" in c}


def test_references_to_photographs_or_observations_are_not_accepted(client, project, headers):
    for field in ("asset_id", "observation_id", "photo_id"):
        response = client.post(
            base(project), json={"kind": "point", "geometry": POINT, field: UNKNOWN_UUID}, headers=headers
        )
        assert response.status_code == 422, field
    assert table_rows() == 0


def test_an_over_long_note_or_one_with_a_nul_character_is_refused(client, project, headers):
    assert make(client, project, headers, note="x" * 2000).status_code == 201
    assert make(client, project, headers, note="x" * 2001).status_code == 422
    assert make(client, project, headers, note="bad\x00note").status_code == 422
    assert table_rows() == 1


def test_coordinates_are_rounded_to_a_fixed_precision_and_returned_as_stored(client, project, headers):
    item = created(client, project, headers, geometry={"type": "Point", "coordinates": [75.123456789012, 27.000000004]})
    assert item["geometry"]["coordinates"] == [75.12345679, 27.0]
    assert client.get(f"{base(project)}/{item['annotation_id']}").json()["geometry"] == item["geometry"]


def test_a_refused_shape_is_audited_without_the_shape_being_stored(client, project, headers):
    make(client, project, headers, kind="polygon", geometry=BOWTIE)
    events = rows("SELECT action, details FROM audit_event WHERE action LIKE 'annotation.%'")
    assert [e[0] for e in events] == ["annotation.refused"]
    assert events[0][1]["code"] == "ANNOTATION_GEOMETRY_INVALID" and events[0][1]["action"] == "create"
    assert "coordinates" not in json.dumps(events[0][1])
    assert table_rows() == 0


# --- editing and history --------------------------------------------------------------------------------------------


def edit(client, project, headers, item, **body):
    return client.patch(
        f"{base(project)}/{item['annotation_id']}",
        json={"expected_version": item["version_no"], **body},
        headers=headers,
    )


def test_editing_the_note_or_the_shape_adds_a_version_and_keeps_the_old_ones(client, project, headers):
    item = created(client, project, headers, kind="line", note="first")
    moved = {"type": "LineString", "coordinates": [[75.0, 27.0], [75.02, 27.0], [75.02, 27.01]]}

    v2 = edit(client, project, headers, item, note="second", reason="clarified")
    assert v2.status_code == 200, v2.text
    assert (v2.json()["version_no"], v2.json()["note"], v2.json()["geometry"]) == (2, "second", LINE)

    v3 = edit(client, project, headers, v2.json(), geometry=moved)
    assert v3.status_code == 200, v3.text
    assert (v3.json()["version_no"], v3.json()["note"], v3.json()["geometry"]) == (3, "second", moved)
    assert v3.json()["measurement"]["length_m"] > item["measurement"]["length_m"]

    history = client.get(f"{base(project)}/{item['annotation_id']}/history").json()
    assert [v["version_no"] for v in history["versions"]] == [1, 2, 3]
    assert [v["action"] for v in history["versions"]] == ["create", "update", "update"]
    assert [v["note"] for v in history["versions"]] == ["first", "second", "second"]
    assert [v["geometry"] for v in history["versions"]] == [LINE, LINE, moved]
    assert history["versions"][1]["reason"] == "clarified" and history["versions"][0]["reason"] is None
    assert {v["actor_label"] for v in history["versions"]} == {"Test Reviewer"}
    assert table_rows() == 3
    # the list shows only the current version
    assert [i["version_no"] for i in client.get(base(project)).json()["items"]] == [3]


def test_a_note_can_be_cleared_and_the_creator_is_kept_when_someone_else_edits(client, project, headers):
    item = created(client, project, headers, note="text")
    other = client.post("/api/v1/workspace/profiles", json={"display_name": "Editor Two"}).json()
    edited = edit(client, project, {"X-Workspace-Profile": other["id"]}, item, note="").json()
    assert edited["note"] == ""
    assert (edited["created_by"], edited["updated_by"]) == ("Test Reviewer", "Editor Two")


def test_an_edit_made_on_an_old_version_is_refused_and_changes_nothing(client, project, headers):
    item = created(client, project, headers, note="first")
    assert edit(client, project, headers, item, note="mine").status_code == 200
    stale = edit(client, project, headers, item, note="theirs")  # still based on version 1
    assert stale.status_code == 409 and stale.json()["code"] == "ANNOTATION_VERSION_CONFLICT"
    assert client.get(f"{base(project)}/{item['annotation_id']}").json()["note"] == "mine"
    assert table_rows() == 2


def test_an_edit_cannot_change_the_kind_or_be_empty_or_repeat_the_current_version(client, project, headers):
    item = created(client, project, headers, kind="line", note="n")
    mismatch = edit(client, project, headers, item, geometry=POINT)
    assert (mismatch.status_code, mismatch.json()["code"]) == (422, "ANNOTATION_KIND_MISMATCH")
    nothing = edit(client, project, headers, item)
    assert (nothing.status_code, nothing.json()["code"]) == (422, "ANNOTATION_NOTHING_TO_CHANGE")
    same = edit(client, project, headers, item, note="n", geometry=LINE)
    assert (same.status_code, same.json()["code"]) == (422, "ANNOTATION_NOTHING_TO_CHANGE")
    invalid = edit(client, project, headers, item, geometry={"type": "LineString", "coordinates": [[75, 27]]})
    assert invalid.status_code == 422
    assert edit(client, project, headers, item, note="n", crs_id="EPSG:3857", geometry=LINE).status_code == 422
    assert table_rows() == 1


def test_editing_needs_a_profile(client, project, headers):
    item = created(client, project, headers)
    response = client.patch(f"{base(project)}/{item['annotation_id']}", json={"expected_version": 1, "note": "x"})
    assert response.status_code == 401 and table_rows() == 1


# --- soft delete ----------------------------------------------------------------------------------------------------


def delete(client, project, headers, item, **params):
    return client.delete(
        f"{base(project)}/{item['annotation_id']}",
        params={"expected_version": item["version_no"], **params},
        headers=headers,
    )


def test_deleting_hides_the_annotation_from_the_normal_list_but_keeps_every_version(client, project, headers):
    keep = created(client, project, headers, note="keep")
    gone = created(client, project, headers, kind="line", note="gone")
    edited = edit(client, project, headers, gone, note="gone, edited").json()

    response = delete(client, project, headers, edited, reason="drawn in the wrong place")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["version_no"], body["deleted_by"]) == ("deleted", 3, "Test Reviewer")
    assert body["deleted_at"] and body["note"] == "gone, edited" and body["geometry"] == LINE

    normal = client.get(base(project)).json()
    assert [i["note"] for i in normal["items"]] == ["keep"]  # excluded by default
    assert (normal["active"], normal["deleted"], normal["include_deleted"]) == (1, 1, False)

    everything = client.get(base(project), params={"include_deleted": "true"}).json()
    assert [(i["note"], i["status"]) for i in everything["items"]] == [("keep", "active"), ("gone, edited", "deleted")]
    assert everything["include_deleted"] is True

    assert table_rows() == 4  # nothing was removed
    history = client.get(f"{base(project)}/{gone['annotation_id']}/history").json()
    assert [(v["action"], v["status"]) for v in history["versions"]] == [
        ("create", "active"),
        ("update", "active"),
        ("delete", "deleted"),
    ]
    assert history["versions"][-1]["reason"] == "drawn in the wrong place"
    assert client.get(f"{base(project)}/{keep['annotation_id']}").json()["status"] == "active"
    # a deleted annotation can still be opened by id, and says it is deleted
    assert client.get(f"{base(project)}/{gone['annotation_id']}").json()["status"] == "deleted"


def test_a_deleted_annotation_cannot_be_deleted_or_edited_again(client, project, headers):
    item = created(client, project, headers)
    deleted = delete(client, project, headers, item).json()
    again = delete(client, project, headers, deleted)
    assert (again.status_code, again.json()["code"]) == (409, "ANNOTATION_DELETED")
    editing = edit(client, project, headers, deleted, note="revive")
    assert (editing.status_code, editing.json()["code"]) == (409, "ANNOTATION_DELETED")
    assert table_rows() == 2


def test_deleting_a_version_you_have_not_seen_is_refused(client, project, headers):
    item = created(client, project, headers)
    edit(client, project, headers, item, note="changed by someone else")
    stale = delete(client, project, headers, item)  # expects version 1
    assert (stale.status_code, stale.json()["code"]) == (409, "ANNOTATION_VERSION_CONFLICT")
    assert client.get(base(project)).json()["active"] == 1
    assert (
        client.delete(f"{base(project)}/{item['annotation_id']}", headers=headers).status_code == 422
    )  # version needed
    assert client.delete(f"{base(project)}/{item['annotation_id']}", params={"expected_version": 2}).status_code == 401


def test_rows_cannot_be_changed_or_removed_in_the_database(client, project, headers):
    created(client, project, headers)
    for statement in ("UPDATE map_annotation SET note = 'changed'", "DELETE FROM map_annotation"):
        with get_engine().begin() as conn, pytest.raises(DBAPIError):
            conn.execute(text(statement))
    assert table_rows() == 1


# --- project isolation ----------------------------------------------------------------------------------------------


def test_annotations_never_cross_projects(client, project, headers):
    mine = created(client, project, headers, note="mine")
    other = other_project(client, headers)
    theirs = created(client, other, headers, kind="line", note="theirs")

    assert [i["note"] for i in client.get(base(project)).json()["items"]] == ["mine"]
    assert [i["note"] for i in client.get(base(other)).json()["items"]] == ["theirs"]

    cross = f"{base(other)}/{mine['annotation_id']}"
    assert client.get(cross).status_code == 404
    assert client.get(f"{cross}/history").status_code == 404
    patched = client.patch(cross, json={"expected_version": 1, "note": "hijack"}, headers=headers)
    assert (patched.status_code, patched.json()["code"]) == (404, "ANNOTATION_NOT_FOUND")
    assert client.delete(cross, params={"expected_version": 1}, headers=headers).status_code == 404
    # and the other way round
    cross = f"{base(project)}/{theirs['annotation_id']}"
    assert client.get(cross).status_code == 404
    assert client.patch(cross, json={"expected_version": 1, "note": "x"}, headers=headers).status_code == 404

    assert client.get(f"{base(project)}/{mine['annotation_id']}").json()["note"] == "mine"
    assert client.get(f"{base(project)}/{mine['annotation_id']}").json()["version_no"] == 1
    assert client.get(f"{base(other)}/{theirs['annotation_id']}").json()["version_no"] == 1
    assert table_rows() == 2


def test_deleting_in_one_project_does_not_touch_the_other_and_counts_are_per_project(client, project, headers):
    other = other_project(client, headers)
    a = created(client, project, headers)
    created(client, other, headers)
    delete(client, project, headers, a)
    assert client.get(base(project)).json()["deleted"] == 1
    assert client.get(base(other)).json()["deleted"] == 0 and client.get(base(other)).json()["active"] == 1


def test_an_unknown_annotation_id_is_a_404_with_a_code(client, project, headers):
    for method, url, kwargs in (
        ("get", f"{base(project)}/{UNKNOWN_UUID}", {}),
        ("get", f"{base(project)}/{UNKNOWN_UUID}/history", {}),
        (
            "patch",
            f"{base(project)}/{UNKNOWN_UUID}",
            {"json": {"expected_version": 1, "note": "x"}, "headers": headers},
        ),
        ("delete", f"{base(project)}/{UNKNOWN_UUID}", {"params": {"expected_version": 1}, "headers": headers}),
    ):
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 404, url
        assert response.json()["code"] == "ANNOTATION_NOT_FOUND", url


# --- separation from observations, photograph locations, exports and the report -------------------------------------


def test_annotations_are_not_observations_photo_locations_or_csv_rows(client, project, headers, run_jobs):
    """Annotations have their own report section and export members (test_annotation_reporting.py); everything else,
    and every observation-related part of the report and exports, is exactly as it was without them."""
    asset, _, _ = photo_with(client, project, headers, name="a.jpg", seed=1)
    assert change(client, headers, asset["id"], action="confirm_exif", expected_version=1).status_code == 201
    run_jobs()
    assert analyze(client, project, headers, asset["id"]).status_code == 202
    run_jobs()

    pid = project["id"]
    urls = {
        "observations": f"/api/v1/projects/{pid}/observations",
        "locations": f"/api/v1/projects/{pid}/photo-locations",
        "assets": f"/api/v1/projects/{pid}/assets",
        "csv": f"/api/v1/projects/{pid}/exports/observations.csv",
        "layers": f"/api/v1/projects/{pid}/layers",
    }

    def snapshot():
        return {k: client.get(u, headers=headers).content for k, u in urls.items()}

    def observation_parts():
        """What the geojson, json and report say about observations, photographs and conflicts: not the annotations."""
        geo = client.get(f"/api/v1/projects/{pid}/exports/observations.geojson", headers=headers).json()
        full = client.get(f"/api/v1/projects/{pid}/exports/observations.json", headers=headers).json()
        body = client.get(f"/api/v1/projects/{pid}/report", headers=headers).json()
        summary = {k: v for k, v in body["summary"].items() if not k.startswith("annotations_")}
        return json.dumps(
            {
                "geo_features": [
                    f for f in geo["features"] if f["properties"].get("feature_type") != "user_annotation"
                ],
                "geo_excluded": geo["excluded"],
                "geo_photographs": geo["photographs"],
                "json": {k: full[k] for k in ("observations", "conflicts", "photo_locations", "runs")},
                "report": {k: body[k] for k in ("assets", "runs", "observations", "photo_locations", "conflicts")},
                "summary": summary,
            },
            sort_keys=True,
        )

    before, before_parts, before_counts = snapshot(), observation_parts(), counts()

    marker = "ZZ-ANNOTATION-MARKER"
    for kind in ("point", "line", "polygon"):
        item = created(client, project, headers, kind=kind, note=marker)
    second = edit(client, project, headers, item, note=marker + " edited").json()
    delete(client, project, headers, second)

    assert snapshot() == before  # not one byte of these listings or of the CSV changed
    assert observation_parts() == before_parts
    assert counts() == before_counts
    for content in snapshot().values():
        assert marker.encode() not in content and b"user_annotation" not in content
    assert marker in client.get(base(project), params={"include_deleted": "true"}).text


def test_annotation_operations_do_not_reach_observation_or_location_tables(client, project, headers):
    before = counts()
    item = created(client, project, headers, kind="polygon")
    second = edit(client, project, headers, item, note="x").json()
    delete(client, project, headers, second)
    assert counts() == before


# --- audit ----------------------------------------------------------------------------------------------------------


def test_every_change_is_audited_with_who_what_and_the_request_and_reads_write_nothing(client, project, headers):
    item = created(client, project, headers, kind="line", note="a private note")
    v2 = edit(client, project, headers, item, note="another private note", geometry=LINE).json()
    delete(client, project, headers, v2, reason="reason text")

    events = rows(
        "SELECT e.action, e.entity_type, e.entity_id, u.display_name, e.request_id, e.details FROM audit_event e "
        "JOIN workspace_user u ON u.id = e.actor_id WHERE e.action LIKE 'annotation.%' ORDER BY e.at, e.id"
    )
    assert [e[0] for e in events] == ["annotation.create", "annotation.update", "annotation.delete"]
    for event in events:
        assert event[1] == "map_annotation" and str(event[2]) == item["annotation_id"]
        assert event[3] == "Test Reviewer" and event[4]
        assert event[5]["project_id"] == project["id"] and event[5]["kind"] == "line"
    assert [e[5]["version"] for e in events] == [1, 2, 3]
    assert events[1][5]["changed"] == ["note"]  # the shape was sent but is the same as before
    assert len({e[4] for e in events}) == 3
    # notes and reasons are in the history, not copied into the audit log
    assert "private note" not in json.dumps([e[5] for e in events]) and "reason text" not in json.dumps(
        [e[5] for e in events]
    )

    before = rows("SELECT count(*) FROM audit_event")[0][0]
    client.get(base(project))
    client.get(base(project), params={"include_deleted": "true"})
    client.get(f"{base(project)}/{item['annotation_id']}")
    client.get(f"{base(project)}/{item['annotation_id']}/history")
    assert rows("SELECT count(*) FROM audit_event")[0][0] == before


def test_a_conflicting_or_missing_annotation_edit_is_not_audited_as_a_change(client, project, headers):
    item = created(client, project, headers)
    edit(client, project, headers, item, note="mine")
    edit(client, project, headers, item, note="stale")  # 409
    client.patch(f"{base(project)}/{UNKNOWN_UUID}", json={"expected_version": 1, "note": "x"}, headers=headers)
    actions = [r[0] for r in rows("SELECT action FROM audit_event WHERE action LIKE 'annotation.%' ORDER BY at")]
    assert actions == ["annotation.create", "annotation.update"]


# --- a database that has not been updated ---------------------------------------------------------------------------


def test_a_database_without_the_annotation_table_answers_503_not_a_server_error():
    class NoTable:
        def execute(self, *_args, **_kwargs):
            class Result:
                @staticmethod
                def scalar_one():
                    return None

            return Result()

    with pytest.raises(ProblemError) as caught:
        annotations_available(NoTable())  # type: ignore[arg-type]
    assert caught.value.status == 503 and caught.value.code == "ANNOTATIONS_NOT_MIGRATED"


def test_annotations_are_available_in_the_migrated_test_database(client, project):
    assert client.get(base(project)).status_code == 200


# --- media/uploads are untouched ------------------------------------------------------------------------------------


def test_annotating_never_touches_stored_originals(client, project, headers):
    upload(client, project, headers, "p.jpg", media.image_bytes("JPEG", seed=4))
    before = rows("SELECT blob_sha256 FROM asset")
    created(client, project, headers)
    assert rows("SELECT blob_sha256 FROM asset") == before
    assert uuid.UUID(project["id"])
