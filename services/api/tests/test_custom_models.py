"""Uploaded ("custom") detection models: the upload check, the registry, removal, and the ONNX decoding.

The real check (a child process that opens and test-runs the file) is exercised once with a tiny ONNX model built for
these tests (tests/fixtures/fake-yolo-3class.onnx: a fixed YOLOv8-layout output with a 'truck' at 0.9, an overlapping
duplicate at 0.8, a 'tent' at 0.3 and a 'tank' at 0.15). Elsewhere the check is replaced, so no model library loads.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from geopoc.api.routers import custom_models as router
from geopoc.pipelines.adapters import onnx_detection as onnx
from geopoc.pipelines.adapters.torchvision_detection import CLASS_HEAD, category_names, extract_state_dict
from geopoc.registry import custom_models
from geopoc.registry.custom_models import CustomModelError
from tests.test_ground_pipeline import analyze, observations, photo

FIXTURE = Path(__file__).parent / "fixtures" / "fake-yolo-3class.onnx"
URL = "/api/v1/models/custom"


def fake_check(answer):
    def check(runtime, path, settings, timeout_s):
        assert Path(path).is_file()
        return answer if not callable(answer) else answer(runtime, path, settings)

    return check


@pytest.fixture
def checked(monkeypatch):
    """Replace the child-process check with one that accepts the file and reports three classes."""
    monkeypatch.setattr(
        router,
        "run_check",
        fake_check({"ok": True, "classes": ["tank", "truck", "tent"], "settings": {"output_layout": "v8"}}),
    )


def upload(client, headers, content=b"model-bytes", **params):
    query = {"filename": "best.onnx", "name": "my-detector", **params}
    return client.post(URL, params=query, content=content, headers=headers)


def models(client):
    return {f"{m['name']}@{m['version']}": m for m in client.get("/health/models").json()["models"]}


# --- registry helpers -------------------------------------------------------------------------------------------------


def test_runtime_follows_the_extension_unless_a_format_is_chosen():
    assert custom_models.runtime_for("a.onnx", None) == "onnx"
    assert custom_models.runtime_for("a.PT", "auto") == "ultralytics"
    assert custom_models.runtime_for("a.pth", None) == "torchvision"
    assert custom_models.runtime_for("a.pt", "torchvision") == "torchvision"
    with pytest.raises(CustomModelError):
        custom_models.runtime_for("a.onnx", "torchvision")
    with pytest.raises(CustomModelError):
        custom_models.runtime_for("a.h5", None)


def test_class_names_are_read_one_per_line_or_comma_separated():
    assert custom_models.parse_classes("tank, truck\n\n tent \n") == ["tank", "truck", "tent"]
    assert custom_models.parse_classes(None) == []
    with pytest.raises(CustomModelError):
        custom_models.parse_classes("x" * 101)


# --- upload and removal -----------------------------------------------------------------------------------------------


def test_an_uploaded_model_is_listed_and_can_be_chosen(client, headers, checked):
    response = upload(client, headers, classes="tank\ntruck\ntent", min_score=0.4, licence="Ours")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["name"], body["version"], body["runtime"], body["custom"]) == ("my-detector", "v1", "onnx", True)
    assert body["original_filename"] == "best.onnx" and body["licence"] == "Ours"
    assert body["settings"]["classes"] == ["tank", "truck", "tent"]
    assert body["settings"]["min_score"] == 0.4 and body["settings"]["output_layout"] == "v8"
    assert body["score_calibrated"] is False and len(body["weights_sha256"]) == 64

    listed = models(client)["my-detector@v1"]
    assert listed["custom"] is True and listed["pipeline"] == "ground_image"
    stored = custom_models.find("my-detector", "v1")
    assert stored is not None and stored.absolute_file().read_bytes() == b"model-bytes"


def test_name_and_content_must_be_new(client, headers, checked):
    assert upload(client, headers).status_code == 201
    taken = upload(client, headers, content=b"other")
    assert (taken.status_code, taken.json()["code"]) == (409, "MODEL_NAME_TAKEN")
    same_file = upload(client, headers, name="another")
    assert (same_file.status_code, same_file.json()["code"]) == (409, "MODEL_DUPLICATE")
    manifest_name = upload(client, headers, content=b"x", name="stub-detector", version="test")
    assert (manifest_name.status_code, manifest_name.json()["code"]) == (409, "MODEL_NAME_TAKEN")
    assert len(list(custom_models.custom_root().glob("*/*"))) == 1  # refused uploads leave nothing behind


def test_a_yolo_pt_file_needs_the_uploader_to_trust_it(client, headers, checked):
    refused = upload(client, headers, filename="best.pt")
    assert (refused.status_code, refused.json()["code"]) == (422, "TRUST_REQUIRED")
    accepted = upload(client, headers, filename="best.pt", trust_code="true")
    assert accepted.status_code == 201 and accepted.json()["runtime"] == "ultralytics"


@pytest.mark.parametrize(
    ("params", "status", "code"),
    [
        ({"filename": "model.h5"}, 415, "UNSUPPORTED_FILE_TYPE"),
        ({"name": "Bad Name!"}, 422, "INVALID_MODEL_NAME"),
        ({"format": "torchvision"}, 415, "UNSUPPORTED_FILE_TYPE"),
    ],
)
def test_invalid_requests_are_refused(client, headers, checked, params, status, code):
    response = upload(client, headers, **params)
    assert (response.status_code, response.json()["code"]) == (status, code)


def test_an_empty_file_is_refused(client, headers, checked):
    response = upload(client, headers, content=b"")
    assert (response.status_code, response.json()["code"]) == (422, "EMPTY_FILE")


def test_a_model_that_fails_its_check_is_not_stored(client, headers, monkeypatch):
    monkeypatch.setattr(router, "run_check", fake_check({"ok": False, "error": "the file does not contain names."}))
    response = upload(client, headers)
    assert (response.status_code, response.json()["code"]) == (422, "MODEL_CHECK_FAILED")
    assert response.json()["detail"] == "The file does not contain names. Nothing was stored."
    assert "my-detector@v1" not in models(client)
    assert not list(custom_models.custom_root().glob("*/*"))


def test_torchvision_architecture_is_passed_to_the_check(client, headers, monkeypatch):
    seen = {}

    def answer(runtime, path, settings):
        seen.update(settings, runtime=runtime)
        return {"ok": True, "classes": ["a"], "settings": {"architecture": settings["architecture"], "num_classes": 2}}

    monkeypatch.setattr(router, "run_check", fake_check(answer))
    response = upload(
        client, headers, filename="frcnn.pth", architecture="fasterrcnn_mobilenet_v3_large_320_fpn", classes="a"
    )
    assert response.status_code == 201, response.text
    assert seen["runtime"] == "torchvision" and seen["classes"] == ["a"]
    assert response.json()["settings"]["architecture"] == "fasterrcnn_mobilenet_v3_large_320_fpn"


def test_removing_a_model_deletes_it_and_its_file(client, headers, checked):
    upload(client, headers)
    folder = custom_models.find("my-detector", "v1").absolute_file().parent
    response = client.delete(f"{URL}/my-detector/v1", headers=headers)
    assert response.status_code == 204
    assert "my-detector@v1" not in models(client) and not folder.exists()
    again = client.delete(f"{URL}/my-detector/v1", headers=headers)
    assert (again.status_code, again.json()["code"]) == (404, "MODEL_NOT_FOUND")
    builtin = client.delete(f"{URL}/stub-detector/test", headers=headers)
    assert builtin.status_code == 404  # models from the manifest cannot be removed here


def test_a_model_in_use_by_a_queued_job_cannot_be_removed(client, project, headers, checked, run_jobs):
    upload(client, headers)
    asset = photo(client, project, headers, run_jobs)
    assert (
        analyze(client, project, headers, asset["id"], model={"name": "my-detector", "version": "v1"}).status_code
        == 202
    )
    response = client.delete(f"{URL}/my-detector/v1", headers=headers)
    assert (response.status_code, response.json()["code"]) == (409, "MODEL_IN_USE")


def test_uploads_and_removals_are_audited(client, headers, checked):
    upload(client, headers)
    client.delete(f"{URL}/my-detector/v1", headers=headers)
    from sqlalchemy import text

    from geopoc.db.session import get_engine

    with get_engine().connect() as conn:
        actions = conn.execute(text("SELECT action FROM audit_event WHERE entity_type = 'model' ORDER BY at")).all()
    assert [a for (a,) in actions] == ["model.custom_added", "model.custom_removed"]


# --- the real check and a real analysis with an uploaded ONNX model ---------------------------------------------------


@pytest.mark.skipif(importlib.util.find_spec("onnxruntime") is None, reason="ONNX Runtime is not installed")
def test_an_uploaded_onnx_model_runs_a_photo_analysis(client, project, headers, run_jobs):
    response = upload(client, headers, content=FIXTURE.read_bytes(), filename="fake-yolo-3class.onnx")
    assert response.status_code == 201, response.text
    assert response.json()["settings"]["classes"] == ["tank", "truck", "tent"]  # read from the file's metadata

    asset = photo(client, project, headers, run_jobs, size=(640, 480))
    queued = analyze(client, project, headers, asset["id"], model={"name": "my-detector", "version": "v1"})
    assert queued.status_code == 202, queued.text
    assert run_jobs() == ["needs_review"]
    detail = client.get(f"/api/v1/jobs/{queued.json()['id']}").json()
    assert detail["result"]["observations"] == 2 and detail["result"]["below_min_score"] == 1

    found = sorted(observations(client, project, asset_id=asset["id"]), key=lambda o: -o["score"]["raw_value"])
    assert [o["claim"]["category"] for o in found] == ["custom_my_detector:truck", "custom_my_detector:tent"]
    # 640 x 480 letterboxed into 640 x 640 is padded 80 px top and bottom; the box comes back in photo pixels.
    assert found[0]["evidence"][0]["region_px"]["coordinates"] == [270.0, 200.0, 370.0, 280.0]
    assert found[0]["model"]["name"] == "my-detector"


# --- ONNX output decoding ---------------------------------------------------------------------------------------------


def test_class_names_come_from_export_metadata():
    assert onnx.read_names({"names": "{0: 'a', 1: 'b', 10: 'k'}"}) == ["a", "b", "k"]
    assert onnx.read_names({"names": "['x', 'y']"}) == ["x", "y"]
    assert onnx.read_names({"names": "__import__('os')"}) == []  # literals only, never evaluated
    assert onnx.read_names({}) == []


def test_input_size_comes_from_the_graph_or_the_export():
    assert onnx.input_size([1, 3, 480, 640], {}) == (640, 480)
    assert onnx.input_size(["batch", 3, "h", "w"], {"imgsz": "[320, 416]"}) == (416, 320)
    assert onnx.input_size(["batch", 3, "h", "w"], {}) == (640, 640)


def test_output_layouts_are_recognised():
    assert onnx.output_layout((1, 84, 8400), 80) == "v8"
    assert onnx.output_layout((1, 116, 8400), 80) == "v8"  # segmentation: 32 mask coefficients follow
    assert onnx.output_layout((1, 25200, 85), 80) == "v5"
    assert onnx.output_layout((1, 25200, 6), 1) == "v5"
    assert onnx.output_layout((1, 300, 6), 80) == "end2end"
    assert onnx.output_layout((1, 7, 8400), 0) == "v8"
    with pytest.raises(ValueError):
        onnx.output_layout((1, 84, 8400), 3)
    with pytest.raises(ValueError):
        onnx.output_layout((1, 8400), 80)


def test_v5_scores_are_objectness_times_class_score():
    pred = np.zeros((1, 2, 7), dtype=np.float32)
    pred[0, 0] = [50, 50, 20, 20, 0.5, 0.9, 0.1]
    pred[0, 1] = [10, 10, 4, 4, 0.1, 0.9, 0.1]
    boxes, scores, ids = onnx.decode(pred, "v5", 2, 0.2)
    assert boxes.tolist() == [[40, 40, 60, 60]] and scores.tolist() == pytest.approx([0.45]) and ids.tolist() == [0]


def test_end_to_end_output_is_used_as_is():
    pred = np.array([[[10, 20, 30, 40, 0.8, 2], [0, 0, 1, 1, 0.01, 0]]], dtype=np.float32)
    detections, below = onnx.postprocess(
        pred,
        layout="end2end",
        classes=["a", "b", "c"],
        scale=1.0,
        pad=(0, 0),
        image_size=(100, 100),
        min_score=0.25,
        iou_threshold=0.45,
        max_detections=10,
    )
    assert [(d.label, d.box) for d in detections] == [("c", (10.0, 20.0, 30.0, 40.0))] and below == 0


def test_non_maximum_suppression_is_per_class():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 10, 10], [0, 0, 10, 10]], dtype=np.float32)
    kept = onnx.nms(boxes, np.array([0.9, 0.8, 0.7], dtype=np.float32), np.array([0, 0, 1]), 0.45)
    assert kept.tolist() == [0, 2]


# --- torchvision weights ----------------------------------------------------------------------------------------------


def test_torchvision_weights_are_found_in_checkpoints():
    weights = {f"module.{CLASS_HEAD}": 1, "module.backbone.x": 2}
    assert extract_state_dict({"model": weights, "epoch": 3}) == {CLASS_HEAD: 1, "backbone.x": 2}
    assert extract_state_dict({CLASS_HEAD: 1}) == {CLASS_HEAD: 1}
    with pytest.raises(ValueError):
        extract_state_dict({"backbone.x": 1})


def test_torchvision_class_names_account_for_the_background_class():
    assert category_names(["a", "b"], 3, []) == ["__background__", "a", "b"]
    assert category_names(["bg", "a", "b"], 3, []) == ["bg", "a", "b"]
    assert category_names([], 91, ["coco"] * 91) == ["coco"] * 91
    with pytest.raises(ValueError, match="2 class names are needed but 1 were given"):
        category_names(["a"], 3, [])
