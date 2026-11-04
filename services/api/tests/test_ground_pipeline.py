"""Phase 3: the ground-image pipeline, exercised with the test-only stub detector.

These tests prove the pipeline's plumbing and contract enforcement. They say nothing about any real model's
quality; the real model is exercised separately in test_gpu_model.py when the hardware and weights are present.
"""

import os
import stat
import uuid

import pytest
from sqlalchemy import text

from geopoc.config import REPO_ROOT, get_settings
from geopoc.contracts.observation import Observation
from geopoc.db.session import get_engine
from geopoc.pipelines.ground_image import to_source_pixels
from geopoc.registry.manifest import load_manifest
from tests import media
from tests.test_assets import stored_files, upload

STUB = {"name": "stub-detector", "version": "test"}


def analyze(client, project, headers, asset_id, model=STUB, pipeline="ground_image", **extra):
    body = {"kind": "analyze", "asset_id": asset_id, "pipeline": pipeline, "model": model, **extra}
    return client.post(f"/api/v1/projects/{project['id']}/jobs", json=body, headers=headers)


def photo(client, project, headers, run_jobs, size=(64, 48), name="photo.jpg", fmt="JPEG"):
    asset = upload(client, project, headers, name, media.image_bytes(fmt, size=size)).json()["asset"]
    run_jobs()  # the automatic preview job
    return asset


def observations(client, project, **params):
    """The model's stored records (the listing also carries each one's current review state)."""
    response = client.get(f"/api/v1/projects/{project['id']}/observations", params=params)
    assert response.status_code == 200, response.text
    return [item["observation"] for item in response.json()]


def count(table: str) -> int:
    with get_engine().connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


# --- the full path ----------------------------------------------------------------------------------------------------


def test_analysis_job_produces_validated_evidence_linked_observations(client, project, headers, run_jobs, profile):
    asset = photo(client, project, headers, run_jobs)
    queued = analyze(client, project, headers, asset["id"])
    assert queued.status_code == 202
    job = queued.json()
    assert (job["status"], job["kind"], job["attempt"]) == ("queued", "analyze", 0)
    assert job["payload"] == {"pipeline": "ground_image", "model": STUB}

    assert run_jobs() == ["needs_review"]
    detail = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert detail["status"] == "needs_review" and detail["progress"] == 1.0 and detail["error"] is None
    assert detail["result"]["observations"] == 2
    assert detail["result"]["below_min_score"] == 1  # reported, not silently hidden
    stages = [e["message"] for e in detail["events"] if e["message"].startswith("Stage:")]
    assert stages == [
        f"Stage: {s}."
        for s in ("checking inputs", "loading model", "reading image", "running model", "validating outputs")
    ]

    found = observations(client, project, asset_id=asset["id"])
    assert len(found) == 2
    first = Observation.model_validate(found[0])  # what is stored satisfies the canonical contract
    assert first.claim.category == "test:thing a" and first.claim.claim_type == "inferred"
    assert first.score.raw_value == 0.9 and first.score.calibrated is False
    assert "carries no meaning" in first.score.semantics
    assert first.review.status.value == "needs_review"  # uncalibrated: never auto-accepted
    assert first.quality_flags == ["NOT_GEOREFERENCED", "UNCALIBRATED_SCORE"]

    # Evidence: the source asset and a region in its own pixel grid.
    (evidence,) = first.evidence
    assert str(evidence.asset_id) == asset["id"]
    assert evidence.region_px.coordinates == [6.4, 9.6, 32.0, 38.4]
    assert (evidence.region_px.image_width, evidence.region_px.image_height) == (64, 48)
    assert evidence.region_px.coordinate_space == "source_image_pixels"

    # No location is produced from pixels.
    assert first.geospatial.geometry is None and first.geospatial.crs_id is None
    assert "Geographic location not established" in first.claim.description

    # Provenance back to the bytes and to the run.
    assert first.provenance.source_checksum == asset["sha256"]
    run = client.get(f"/api/v1/runs/{detail['result']['run_id']}").json()
    assert str(first.model.run_id) == run["id"] and run["job_id"] == job["id"]
    assert (run["model_name"], run["model_version"], run["pipeline"]) == ("stub-detector", "test", "ground_image")
    assert run["settings"]["min_score"] == 0.3 and "not a calibrated threshold" in run["settings"]["note"]
    assert run["summary"]["observations"] == 2 and run["summary"]["rejected_outputs"] == []
    assert run["started_at"] <= run["finished_at"]

    assert observations(client, project, run_id=run["id"]) == found
    assert observations(client, project, asset_id=str(uuid.uuid4())) == []

    with get_engine().connect() as conn:
        audit = conn.execute(
            text("SELECT actor_id::text, details FROM audit_event WHERE action = 'job.create' ORDER BY at DESC")
        ).first()
    assert audit[0] == profile["id"] and audit[1]["asset_id"] == asset["id"] and audit[1]["model"] == STUB


def test_boxes_are_reported_in_the_original_pixel_grid_even_when_inference_ran_on_a_smaller_copy(
    client, project, headers, run_jobs
):
    asset = photo(client, project, headers, run_jobs, size=(4000, 3000))
    job = analyze(client, project, headers, asset["id"]).json()
    run_jobs()
    run = client.get(f"/api/v1/runs/{client.get('/api/v1/jobs/' + job['id']).json()['result']['run_id']}").json()
    assert max(run["settings"]["inference_size"]) <= 1333  # the model saw a reduced image
    assert run["settings"]["source_size"] == [4000, 3000]

    box = observations(client, project, asset_id=asset["id"])[0]["evidence"][0]["region_px"]
    assert box["coordinates"] == pytest.approx([400, 600, 2000, 2400], abs=0.5)
    assert (box["image_width"], box["image_height"]) == (4000, 3000)


def test_scaling_back_to_source_pixels_is_exact_and_clamped():
    assert to_source_pixels((10, 20, 30, 40), inferred=(100, 50), source=(400, 100)) == [40.0, 40.0, 120.0, 80.0]
    assert to_source_pixels((-5, -5, 120, 60), inferred=(100, 50), source=(400, 100)) == [0.0, 0.0, 400.0, 100.0]


def test_zero_findings_is_a_valid_result(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    job = analyze(client, project, headers, asset["id"], model={"name": "stub-empty", "version": "test"}).json()
    assert run_jobs() == ["completed"]
    detail = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert detail["result"]["observations"] == 0
    assert "valid result" in detail["events"][-1]["message"]
    assert observations(client, project) == []
    assert count("pipeline_run") == 1  # the run is recorded even though it found nothing


def test_model_output_that_breaks_the_contract_is_recorded_and_never_stored(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    job = analyze(
        client, project, headers, asset["id"], model={"name": "stub-invalid-output", "version": "test"}
    ).json()
    assert run_jobs() == ["completed_with_warnings"]
    detail = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert (detail["result"]["observations"], detail["result"]["rejected_outputs"]) == (1, 2)

    stored = observations(client, project)
    assert [o["claim"]["category"] for o in stored] == ["test:good"]
    rejected = client.get(f"/api/v1/runs/{detail['result']['run_id']}").json()["summary"]["rejected_outputs"]
    assert {r["label"] for r in rejected} == {"zero-area box", "score out of range"}
    assert all(r["errors"] for r in rejected)


def test_model_failure_fails_the_job_and_stores_nothing(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    job = analyze(client, project, headers, asset["id"], model={"name": "stub-failing", "version": "test"}).json()
    assert run_jobs() == ["failed"]
    detail = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert detail["error"]["code"] == "INFERENCE_FAILED" and detail["attempt"] == 1
    assert count("observation") == 0 and count("pipeline_run") == 0
    assert detail["has_partial_outputs"] is False


def test_a_tampered_original_is_not_analysed(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    (original,) = stored_files()
    os.chmod(original, stat.S_IWRITE)
    original.write_bytes(media.image_bytes("JPEG", seed=99))
    job = analyze(client, project, headers, asset["id"]).json()
    assert run_jobs() == ["failed"]
    error = client.get(f"/api/v1/jobs/{job['id']}").json()["error"]
    assert error["code"] == "STORAGE_ERROR" and "no longer matches its checksum" in error["message"]
    assert count("observation") == 0


def test_each_run_is_separate_and_earlier_results_are_kept(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    analyze(client, project, headers, asset["id"])
    run_jobs()
    analyze(client, project, headers, asset["id"])
    run_jobs()
    assert count("pipeline_run") == 2 and count("observation") == 4
    runs = {o["model"]["run_id"] for o in observations(client, project)}
    assert len(runs) == 2


# --- what the API refuses ----------------------------------------------------------------------------------------------


def test_only_the_ground_image_and_video_frame_pipelines_exist(client, project, headers, run_jobs):
    raster = upload(client, project, headers, "r.tif", media.geotiff_bytes(), "overhead_raster").json()["asset"]
    asset = photo(client, project, headers, run_jobs)

    response = analyze(client, project, headers, raster["id"], pipeline="overhead_raster")
    assert response.status_code == 422 and response.json()["code"] == "PIPELINE_NOT_AVAILABLE"
    assert "not implemented" in response.json()["detail"]
    # the video pipeline exists, but it does not accept a raster (and needs a sampling choice: see test_video_frames.py)
    not_a_video = analyze(
        client, project, headers, raster["id"], pipeline="video", sampling={"interval_s": 1, "max_frames": 5}
    )
    assert not_a_video.status_code == 422 and not_a_video.json()["code"] == "PIPELINE_INCOMPATIBLE"
    wrong = analyze(client, project, headers, raster["id"])
    assert wrong.status_code == 422 and wrong.json()["code"] == "PIPELINE_INCOMPATIBLE"

    assert (
        analyze(client, project, headers, asset["id"], model={"name": "nope", "version": "1"}).json()["code"]
        == "MODEL_UNKNOWN"
    )
    assert (
        analyze(client, project, headers, asset["id"], model={"name": "cloud-detector", "version": "test"}).json()[
            "code"
        ]
        == "MODEL_UNKNOWN"
    )
    url = f"/api/v1/projects/{project['id']}/jobs"
    assert client.post(url, json={"kind": "analyze", "asset_id": asset["id"]}, headers=headers).status_code == 422
    assert client.post(url, json={"kind": "selftest", "asset_id": asset["id"]}, headers=headers).status_code == 422
    assert (
        client.post(url, json={"kind": "preview", "asset_id": asset["id"], "priority": 1}, headers=headers).status_code
        == 422
    )
    assert count("job") == 3  # only the automatic jobs exist: two previews and the raster's band statistics


def test_job_creation_checks_profile_project_and_asset(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    other = client.post("/api/v1/projects", json={"name": "Other"}, headers=headers).json()

    assert analyze(client, project, {}, asset["id"]).json()["code"] == "PROFILE_REQUIRED"
    assert analyze(client, other, headers, asset["id"]).json()["code"] == "ASSET_NOT_FOUND"  # another project's asset
    assert analyze(client, {"id": str(uuid.uuid4())}, headers, asset["id"]).json()["code"] == "PROJECT_NOT_FOUND"
    assert analyze(client, project, headers, str(uuid.uuid4())).json()["code"] == "ASSET_NOT_FOUND"

    assert analyze(client, project, headers, asset["id"]).status_code == 202
    again = analyze(client, project, headers, asset["id"])
    assert again.status_code == 409 and again.json()["code"] == "JOB_ALREADY_ACTIVE"

    client.patch(f"/api/v1/projects/{project['id']}", json={"archived": True}, headers=headers)
    assert analyze(client, project, headers, asset["id"]).json()["code"] == "PROJECT_ARCHIVED"


def test_a_model_whose_weights_are_not_installed_cannot_be_queued(
    client, project, headers, run_jobs, tmp_path, monkeypatch
):
    asset = photo(client, project, headers, run_jobs)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "models:\n"
        "  - name: real-detector\n    version: v1\n    pipeline: ground_image\n    task: object_detection\n"
        "    runtime: torchvision\n    licence: test\n"
        "    score: {semantics: 'class score', calibrated: false}\n"
        "    weights: {path: weights/none/model.pth, sha256: '" + "0" * 64 + "', size_bytes: 10}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(get_settings(), "manifest_path", manifest)

    response = analyze(client, project, headers, asset["id"], model={"name": "real-detector", "version": "v1"})
    assert response.status_code == 409 and response.json()["code"] == "MODEL_UNAVAILABLE"
    assert "not installed" in response.json()["detail"]

    listed = client.get("/health/models").json()
    (model,) = listed["models"]
    assert model["available"] is False and "not installed" in model["reason_unavailable"]
    assert model["score_calibrated"] is False and model["score_semantics"] == "class score"


# --- cancel, retry, drafts, listing -------------------------------------------------------------------------------------


def test_cancel_retry_and_draft_through_the_api(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    job = analyze(client, project, headers, asset["id"]).json()

    assert client.post(f"/api/v1/jobs/{job['id']}/cancel").json()["code"] == "PROFILE_REQUIRED"
    cancelled = client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=headers)
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["has_partial_outputs"] is False
    assert run_jobs() == []  # it never ran
    twice = client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=headers)
    assert twice.status_code == 409 and twice.json()["code"] == "JOB_ALREADY_FINISHED"

    retried = client.post(f"/api/v1/jobs/{job['id']}/retry", headers=headers)
    assert retried.status_code == 202
    new = retried.json()
    assert new["id"] != job["id"] and new["retry_of_job_id"] == job["id"] and new["status"] == "queued"
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "cancelled"  # the old job and its log remain
    assert run_jobs() == ["needs_review"]
    not_retryable = client.post(f"/api/v1/jobs/{new['id']}/retry", headers=headers)
    assert not_retryable.status_code == 409 and not_retryable.json()["code"] == "JOB_NOT_RETRYABLE"

    draft = analyze(client, project, headers, asset["id"], submit=False)
    assert draft.status_code == 201 and draft.json()["status"] == "draft"
    assert run_jobs() == []
    started = client.post(f"/api/v1/jobs/{draft.json()['id']}/start", headers=headers)
    assert started.status_code == 202 and started.json()["status"] == "queued"
    assert client.post(f"/api/v1/jobs/{draft.json()['id']}/start", headers=headers).json()["code"] == "JOB_NOT_DRAFT"

    url = f"/api/v1/projects/{project['id']}/jobs"
    assert len(client.get(url).json()["items"]) == 4  # preview, cancelled, retried, draft-now-queued
    assert [j["kind"] for j in client.get(url, params={"kind": "preview"}).json()["items"]] == ["preview"]
    assert len(client.get(url, params={"status": "cancelled"}).json()["items"]) == 1
    assert len(client.get(url, params={"asset_id": asset["id"], "kind": "analyze"}).json()["items"]) == 3
    assert client.get(url, params={"limit": 1}).json()["next_cursor"]
    assert client.get(f"/api/v1/jobs/{uuid.uuid4()}").json()["code"] == "JOB_NOT_FOUND"
    assert client.get(f"/api/v1/runs/{uuid.uuid4()}").json()["code"] == "RUN_NOT_FOUND"


# --- storage rules and the manifest ---------------------------------------------------------------------------------------


def test_database_refuses_an_observation_without_evidence_or_with_an_unknown_state(client, project, headers, run_jobs):
    asset = photo(client, project, headers, run_jobs)
    analyze(client, project, headers, asset["id"])
    run_jobs()
    # Since Phase 4 the immutability trigger refuses any change to the stored record before the check runs.
    with get_engine().begin() as conn, pytest.raises(Exception, match="cannot be changed"):
        conn.execute(text("UPDATE observation SET record = jsonb_set(record, '{evidence}', '[]'::jsonb)"))
    with get_engine().begin() as conn, pytest.raises(Exception, match="ck_observation_review_status"):
        conn.execute(text("UPDATE observation SET review_status = 'confirmed'"))


def test_manifest_refuses_remote_and_stub_models_outside_tests(monkeypatch):
    test_manifest = load_manifest()
    assert {m.name for m in test_manifest.models} == {
        "stub-detector",
        "stub-detector-alt",
        "stub-empty",
        "stub-invalid-output",
        "stub-failing",
    }
    assert any("cloud-detector" in e and "not local" in e for e in test_manifest.errors)

    monkeypatch.setattr(get_settings(), "allow_stub_models", False)
    locked_down = load_manifest()
    assert locked_down.models == []
    assert sum("test stub and was refused" in e for e in locked_down.errors) == 5


def test_the_real_manifest_is_valid_and_contains_no_stub_and_only_declared_online_models(monkeypatch):
    monkeypatch.setattr(get_settings(), "manifest_path", REPO_ROOT / "models" / "manifest.yaml")
    monkeypatch.setattr(get_settings(), "allow_stub_models", False)
    manifest = load_manifest()
    assert manifest.found and manifest.errors == []
    for model in manifest.models:
        assert model.runtime != "stub" and model.pipeline == "ground_image"
        assert model.score_semantics and model.score_calibrated is False  # nothing is calibrated yet
        assert model.licence != "not stated"
        if model.locality == "remote":
            # The only way a photo may leave this machine: a runtime built for it, declared as such in the manifest.
            assert model.runtime in {"roboflow", "roboflow_world"} and model.weights_sha256 is None
        else:
            assert model.weights_sha256 and len(model.weights_sha256) == 64


def test_the_ground_pipeline_does_not_depend_on_other_pipelines():
    source = (REPO_ROOT / "services" / "api" / "geopoc" / "pipelines" / "ground_image.py").read_text(encoding="utf-8")
    assert "overhead" not in source.replace("overhead or video pipelines", "") and "pipelines.video" not in source
