"""Read-only overview endpoints used by the dashboard. Every number must come from stored rows."""

import uuid

from tests import media
from tests.test_assets import upload
from tests.test_ground_pipeline import STUB, analyze


def test_new_project_summary_is_all_zero(client, project):
    body = client.get(f"/api/v1/projects/{project['id']}/summary").json()
    assert body["assets_by_modality"] == {"ground_image": 0, "overhead_raster": 0, "video": 0}
    assert body["assets_total"] == 0 and body["observations_total"] == 0
    assert body["jobs_by_status"] == {} and body["observations_by_review_status"] == {}
    assert body["last_activity_at"] == project["updated_at"]


def test_summary_counts_what_is_stored(client, project, headers, run_jobs):
    photo = upload(client, project, headers, "a.jpg", media.image_bytes("JPEG", seed=1)).json()["asset"]
    upload(client, project, headers, "b.png", media.image_bytes("PNG", seed=2))
    upload(client, project, headers, "r.tif", media.geotiff_bytes(), "overhead_raster")
    upload(client, project, headers, "bad.gif", b"GIF89a")  # rejected: never counted
    run_jobs()
    analyze(client, project, headers, photo["id"], model=STUB)
    run_jobs()

    body = client.get(f"/api/v1/projects/{project['id']}/summary").json()
    assert body["assets_by_modality"] == {"ground_image": 2, "overhead_raster": 1, "video": 0}
    assert body["assets_total"] == 3
    assert body["jobs_by_status"] == {"completed": 3, "needs_review": 1}
    assert body["observations_by_review_status"] == {"needs_review": 2}
    assert body["observations_total"] == 2
    assert body["last_activity_at"] > project["updated_at"]

    other = client.post("/api/v1/projects", json={"name": "Other"}, headers=headers).json()
    assert client.get(f"/api/v1/projects/{other['id']}/summary").json()["assets_total"] == 0
    assert client.get(f"/api/v1/projects/{uuid.uuid4()}/summary").json()["code"] == "PROJECT_NOT_FOUND"


def test_recent_jobs_span_projects_and_name_their_project_and_file(client, project, headers, run_jobs):
    assert client.get("/api/v1/jobs").json() == {"items": []}
    upload(client, project, headers, "a.jpg", media.image_bytes("JPEG", seed=3))
    other = client.post("/api/v1/projects", json={"name": "Second"}, headers=headers).json()
    upload(client, other, headers, "b.png", media.image_bytes("PNG", seed=4))
    run_jobs()

    items = client.get("/api/v1/jobs").json()["items"]
    assert [(j["project_name"], j["asset_filename"], j["kind"]) for j in items] == [
        ("Second", "b.png", "preview"),
        ("Ingestion", "a.jpg", "preview"),
    ]
    assert all(j["status"] == "completed" for j in items)
    assert len(client.get("/api/v1/jobs", params={"limit": 1}).json()["items"]) == 1
    assert client.get("/api/v1/jobs", params={"status": "failed"}).json()["items"] == []
    assert client.get("/api/v1/jobs", params={"limit": 500}).status_code == 422
