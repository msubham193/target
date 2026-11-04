"""The online Roboflow detector: gated by the owner's opt-in, never leaks its key, and turns Roboflow's boxes into corners.

No network is used: `httpx.post` is replaced, and the settings are replaced with plain objects.
"""

from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr

from geopoc.pipelines.adapters import roboflow
from geopoc.pipelines.base import load_adapter
from geopoc.registry import manifest as manifest_module
from geopoc.registry.manifest import REMOTE_RUNTIMES, ModelEntry, online_problem, runtime_problem, weights_problem

KEY = "rf_super_secret_key_123"


def entry(**settings) -> ModelEntry:
    return ModelEntry(
        name="roboflow_yolo_online",
        version="hosted_v1",
        pipeline="ground_image",
        task="object_detection",
        runtime="roboflow",
        licence="online",
        score_semantics="model confidence",
        score_calibrated=False,
        score_range=(0.0, 1.0),
        settings={"min_score": 0.4, "max_detections": 100, **settings},
        locality="remote",
    )


def configure(
    monkeypatch, *, online=True, key: str | None = KEY, model_id: str | None = "harbour-objects/3", world_classes=None
):
    fake = SimpleNamespace(
        online_enabled=online,
        roboflow_api_key=SecretStr(key) if key is not None else None,
        roboflow_model_id=model_id,
        roboflow_world_classes=world_classes,
        roboflow_api_url="https://detect.roboflow.com",
        roboflow_timeout_s=5.0,
    )
    monkeypatch.setattr(roboflow, "get_settings", lambda: fake)
    monkeypatch.setattr(manifest_module, "get_settings", lambda: fake)
    return fake


def test_roboflow_is_the_only_runtime_allowed_to_be_remote():
    assert REMOTE_RUNTIMES == {"roboflow", "roboflow_world"}


def test_unavailable_until_the_owner_opts_in(monkeypatch):
    configure(monkeypatch, online=False)
    assert "GEOPOC_ONLINE_ENABLED" in (online_problem(entry()) or "")
    assert weights_problem(entry()) == online_problem(entry())


def test_unavailable_without_a_key_or_a_model_id(monkeypatch):
    configure(monkeypatch, key=None)
    assert "API key" in (online_problem(entry()) or "")
    configure(monkeypatch, model_id=None)
    assert "model" in (online_problem(entry()) or "").lower()
    # a model id may also come from the manifest
    assert online_problem(entry(model_id="a/1")) is None


def test_available_when_configured_and_needs_no_pytorch(monkeypatch):
    configure(monkeypatch)
    assert weights_problem(entry()) is None
    assert runtime_problem(entry(), {"torch": False, "torchvision": False}) is None
    assert isinstance(load_adapter(entry()), roboflow.RoboflowDetector)


def test_boxes_are_converted_from_centre_and_size_to_corners(monkeypatch):
    configure(monkeypatch)
    seen = {}

    def fake_post(url, params=None, content=None, headers=None, timeout=None):
        seen.update(url=url, params=params, size=len(content))
        return httpx.Response(
            200,
            json={
                "predictions": [
                    {"x": 50, "y": 40, "width": 20, "height": 10, "confidence": 0.9, "class": "boat"},
                    {
                        "x": 5,
                        "y": 5,
                        "width": 40,
                        "height": 40,
                        "confidence": 0.8,
                        "class": "dock",
                    },  # runs off the edge
                    {"x": 10, "y": 10, "width": 4, "height": 4, "confidence": 0.1, "class": "buoy"},  # under the cutoff
                    {"x": 1, "class": "broken"},  # malformed: skipped, never a finding
                ]
            },
        )

    monkeypatch.setattr(roboflow.httpx, "post", fake_post)
    detector = load_adapter(entry())
    info = detector.load(entry())
    assert info["online"] is True and info["model_id"] == "harbour-objects/3"
    result = detector.infer(Image.new("RGB", (100, 80), "gray"))

    assert seen["url"] == "https://detect.roboflow.com/harbour-objects/3"
    assert seen["params"]["confidence"] == 40
    labels = {d.label: d.box for d in result.detections}
    assert labels["boat"] == (40.0, 35.0, 60.0, 45.0)
    assert labels["dock"] == (0.0, 0.0, 25.0, 25.0)
    assert set(labels) == {"boat", "dock"}
    assert result.info["online"] is True


@pytest.mark.parametrize(
    ("status", "needle"),
    [(401, "rejected the API key"), (404, "does not know this model"), (429, "limit"), (500, "HTTP 500")],
)
def test_http_failures_are_explained_and_never_contain_the_key(monkeypatch, status, needle):
    configure(monkeypatch)
    monkeypatch.setattr(roboflow.httpx, "post", lambda *a, **k: httpx.Response(status, text=f"oops {KEY}"))
    detector = roboflow.RoboflowDetector()
    detector.load(entry())
    with pytest.raises(RuntimeError) as caught:
        detector.infer(Image.new("RGB", (10, 10)))
    assert needle in str(caught.value)
    assert KEY not in str(caught.value)


def test_a_network_error_never_exposes_the_request_url(monkeypatch):
    configure(monkeypatch)

    def boom(*args, **kwargs):
        raise httpx.ConnectError(f"failed for https://detect.roboflow.com/x/1?api_key={KEY}")

    monkeypatch.setattr(roboflow.httpx, "post", boom)
    detector = roboflow.RoboflowDetector()
    detector.load(entry())
    with pytest.raises(RuntimeError) as caught:
        detector.infer(Image.new("RGB", (10, 10)))
    assert "ConnectError" in str(caught.value)
    assert KEY not in str(caught.value)
    assert caught.value.__suppress_context__  # raised "from None", so the original message is not chained on


def test_load_refuses_when_online_mode_is_off(monkeypatch):
    configure(monkeypatch, online=False)
    with pytest.raises(RuntimeError, match="online mode is off"):
        roboflow.RoboflowDetector().load(entry())


def world_entry(**settings) -> ModelEntry:
    base = entry()
    return ModelEntry(
        **{
            **base.__dict__,
            "name": "roboflow_yolo_world_online",
            "runtime": "roboflow_world",
            "settings": {"min_score": 0.3, "max_detections": 100, "classes": ["car", "bus"], **settings},
        }
    )


def test_world_model_needs_a_key_but_no_model_id(monkeypatch):
    configure(monkeypatch, model_id=None)
    assert online_problem(world_entry()) is None
    configure(monkeypatch, key=None, model_id=None)
    assert "API key" in (online_problem(world_entry()) or "")
    assert isinstance(load_adapter(world_entry()), roboflow.RoboflowWorldDetector)


def test_world_classes_come_from_the_env_when_set_else_the_manifest():
    assert roboflow.world_classes(world_entry(), None) == ["car", "bus"]
    assert roboflow.world_classes(world_entry(), " red truck, solar panel ,, red truck") == ["red truck", "solar panel"]
    assert roboflow.world_classes(world_entry(classes=[]), "  ") == []


def test_world_request_uses_a_header_key_a_json_body_and_the_class_names(monkeypatch):
    configure(monkeypatch, world_classes="drone, tent")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None, **rest):
        seen.update(url=url, headers=headers, json=json, rest=rest)
        return httpx.Response(
            200,
            json={"predictions": [{"x": 50, "y": 40, "width": 20, "height": 10, "confidence": 0.7, "class": "tent"}]},
        )

    monkeypatch.setattr(roboflow.httpx, "post", fake_post)
    detector = roboflow.RoboflowWorldDetector()
    info = detector.load(world_entry())
    result = detector.infer(Image.new("RGB", (100, 80), "gray"))

    assert info["classes"] == ["drone", "tent"] and info["model_id"] == "yolo_world/v2-l"
    assert seen["url"] == "https://detect.roboflow.com/yolo_world/infer"
    assert seen["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert "params" not in seen["rest"]  # the key is not in the URL
    assert seen["json"]["text"] == ["drone", "tent"]
    assert seen["json"]["image"]["type"] == "base64" and seen["json"]["confidence"] == 0.3
    assert [(d.label, d.box) for d in result.detections] == [("tent", (40.0, 35.0, 60.0, 45.0))]


def test_world_refuses_an_empty_class_list(monkeypatch):
    configure(monkeypatch)
    with pytest.raises(RuntimeError, match="no class names"):
        roboflow.RoboflowWorldDetector().load(world_entry(classes=[]))
