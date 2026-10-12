import uuid

import pytest
from sqlalchemy import text

from geopoc.db.session import get_engine

URL = "/api/v1/projects"


def audit_actions(entity_id: str) -> list[str]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT action FROM audit_event WHERE entity_id = :id ORDER BY at"), {"id": entity_id}
        )
        return [row[0] for row in rows]


def test_create_project_without_location(client, headers, profile):
    """Acceptance checklist item 1, first half: a project needs no location."""
    response = client.post(URL, json={"name": "Survey A"}, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Survey A"
    assert body["aoi"] is None
    assert body["tags"] == []
    assert body["source_preference"] == "local_only"
    assert body["created_by"]["id"] == profile["id"]
    assert audit_actions(body["id"]) == ["project.create"]


def test_mutations_require_a_profile(client):
    response = client.post(URL, json={"name": "No profile"})
    assert response.status_code == 401
    assert response.json()["code"] == "PROFILE_REQUIRED"
    unknown = client.post(URL, json={"name": "x"}, headers={"X-Workspace-Profile": str(uuid.uuid4())})
    assert unknown.status_code == 401


def test_reopen_project(client, headers):
    created = client.post(
        URL, json={"name": "Reopen me", "description": "d", "tags": ["a", "b", "a"]}, headers=headers
    ).json()
    fetched = client.get(f"{URL}/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created
    assert created["tags"] == ["a", "b"]


def test_unknown_project_is_a_problem_response(client):
    response = client.get(f"{URL}/{uuid.uuid4()}")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "PROJECT_NOT_FOUND"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_aoi_in_geographic_crs_is_stored_with_its_source(client, headers):
    aoi = {"geometry": {"type": "Point", "coordinates": [77.2, 28.6]}, "crs_id": "epsg:4326"}
    body = client.post(URL, json={"name": "With AOI", "aoi": aoi}, headers=headers).json()
    assert body["aoi"]["source"]["crs_id"] == "EPSG:4326"
    assert body["aoi"]["source"]["geometry"] == aoi["geometry"]
    assert body["aoi"]["geometry_4326"]["coordinates"] == pytest.approx([77.2, 28.6])
    assert body["aoi"]["warnings"] == []


def test_aoi_in_projected_crs_is_transformed_and_provenance_recorded(client, headers):
    # EPSG:32643 = WGS 84 / UTM zone 43N; (500000, 3000000) lies on the central meridian, 75 E.
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [[500000, 3000000], [501000, 3000000], [501000, 3001000], [500000, 3001000], [500000, 3000000]]
        ],
    }
    body = client.post(
        URL, json={"name": "UTM", "aoi": {"geometry": polygon, "crs_id": "EPSG:32643"}}, headers=headers
    ).json()
    lon, lat = body["aoi"]["geometry_4326"]["coordinates"][0][0]
    assert lon == pytest.approx(75.0, abs=1e-6)
    assert 27.0 < lat < 27.2
    source = body["aoi"]["source"]
    assert source["geometry"] == polygon  # the entered coordinates are kept unchanged
    transformation = source["transformation"]
    assert transformation["target_crs_id"] == "EPSG:4326"
    assert transformation["definition"]
    assert transformation["proj_version"] and transformation["pyproj_version"]


def test_aoi_outside_crs_area_of_use_is_accepted_with_warning(client, headers):
    # A UTM 43N CRS used far west of its zone: kept as entered, flagged, not altered.
    aoi = {"geometry": {"type": "Point", "coordinates": [-2000000, 3000000]}, "crs_id": "EPSG:32643"}
    response = client.post(URL, json={"name": "Odd", "aoi": aoi}, headers=headers)
    assert response.status_code == 201
    assert response.json()["aoi"]["warnings"] == ["AOI_OUTSIDE_CRS_AREA_OF_USE"]


@pytest.mark.parametrize("crs_id", ["WGS84 + LCC", "WGS84", "Lambert Conformal Conic", "EPSG:999999999"])
def test_incomplete_or_unknown_crs_is_rejected(client, headers, crs_id):
    """PT-03: a datum name plus a projection method is not a CRS; nothing is guessed."""
    aoi = {"geometry": {"type": "Point", "coordinates": [77.2, 28.6]}, "crs_id": crs_id}
    response = client.post(URL, json={"name": "Bad CRS", "aoi": aoi}, headers=headers)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "CRS_UNRESOLVED"
    assert body["errors"][0]["field"] == "body.aoi.crs_id"
    assert client.get(URL).json()["items"] == []


def test_aoi_requires_a_crs(client, headers):
    aoi = {"geometry": {"type": "Point", "coordinates": [77.2, 28.6]}}
    response = client.post(URL, json={"name": "No CRS", "aoi": aoi}, headers=headers)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("geometry", "code"),
    [
        (
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
            "AOI_GEOMETRY_INVALID",
        ),
        ({"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, "AOI_GEOMETRY_INVALID"),
        ({"type": "Point", "coordinates": ["a", "b"]}, "AOI_GEOMETRY_INVALID"),
        ({"type": "Point", "coordinates": [28.6, 177.2]}, "AOI_COORDINATES_OUT_OF_RANGE"),
    ],
)
def test_invalid_aoi_geometry_is_rejected_not_repaired(client, headers, geometry, code):
    response = client.post(
        URL, json={"name": "Bad", "aoi": {"geometry": geometry, "crs_id": "EPSG:4326"}}, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == code


def test_list_search_tag_filter_and_pagination(client, headers):
    for index in range(5):
        client.post(URL, json={"name": f"Harbour {index}", "tags": ["coast"]}, headers=headers)
    client.post(
        URL, json={"name": "Inland", "description": "100% dry_land", "tags": ["plain"]}, headers=headers
    )

    first = client.get(URL, params={"limit": 4}).json()
    assert len(first["items"]) == 4 and first["next_cursor"]
    second = client.get(URL, params={"limit": 4, "cursor": first["next_cursor"]}).json()
    assert len(second["items"]) == 2 and second["next_cursor"] is None
    ids = [p["id"] for p in first["items"] + second["items"]]
    assert len(set(ids)) == 6
    assert first["items"][0]["name"] == "Inland"  # newest first

    assert len(client.get(URL, params={"q": "harbour"}).json()["items"]) == 5
    assert [p["name"] for p in client.get(URL, params={"q": "dry_land"}).json()["items"]] == ["Inland"]
    assert (
        client.get(URL, params={"q": "%"}).json()["items"][0]["name"] == "Inland"
    )  # literal %, not a wildcard
    assert len(client.get(URL, params={"q": "100% dry"}).json()["items"]) == 1
    assert [p["name"] for p in client.get(URL, params={"tag": "plain"}).json()["items"]] == ["Inland"]

    assert client.get(URL, params={"cursor": "not-a-cursor"}).json()["code"] == "INVALID_CURSOR"


def test_update_archive_and_audit(client, headers):
    project = client.post(
        URL,
        json={
            "name": "Before",
            "aoi": {"geometry": {"type": "Point", "coordinates": [1, 2]}, "crs_id": "EPSG:4326"},
        },
        headers=headers,
    ).json()
    url = f"{URL}/{project['id']}"

    assert client.patch(url, json={"name": "After"}).status_code == 401

    updated = client.patch(url, json={"name": "After", "tags": ["x"], "aoi": None}, headers=headers).json()
    assert updated["name"] == "After" and updated["tags"] == ["x"] and updated["aoi"] is None
    assert updated["updated_at"] > project["updated_at"]

    archived = client.patch(url, json={"archived": True}, headers=headers).json()
    assert archived["archived_at"] is not None
    assert client.get(URL).json()["items"] == []
    assert [p["id"] for p in client.get(URL, params={"archived": True}).json()["items"]] == [project["id"]]
    assert client.get(url).status_code == 200  # still reopenable

    assert client.patch(url, json={"name": None}, headers=headers).status_code == 422
    assert client.patch(url, json={"unknown_field": 1}, headers=headers).status_code == 422
    assert audit_actions(project["id"]) == ["project.create", "project.update", "project.update"]


def test_text_that_the_database_cannot_store_is_rejected(client, headers):
    response = client.post(URL, json={"name": "bad\u0000name"}, headers=headers)
    assert response.status_code == 422
