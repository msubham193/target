"""Phase 3: the real model on the real GPU.

These tests need PyTorch with CUDA, a CUDA device, and the model weights installed. Where any of those is
missing they are SKIPPED, with the reason shown, and must then be reported as not run.

They check that the inference path works and that its output satisfies the contract. They do not measure
accuracy: no labelled evaluation data exists, and the input here is a synthetic image with no real objects.
"""

import importlib.util
import socket

import pytest

from geopoc.config import REPO_ROOT, get_settings
from tests import media
from tests.conftest import TEST_DATA_ROOT
from tests.test_assets import upload
from tests.test_ground_pipeline import analyze, observations

MODEL = {"name": "fasterrcnn_resnet50_fpn_v2", "version": "coco_v1"}
REAL_DATA_ROOT = TEST_DATA_ROOT.parent  # the configured data folder, where installed weights live


def _why_not() -> str | None:
    if importlib.util.find_spec("torch") is None or importlib.util.find_spec("torchvision") is None:
        return "PyTorch/torchvision are not installed (uv sync --group ml)"
    import torch

    if not torch.cuda.is_available():
        return "no CUDA device is available to PyTorch"
    weights = REAL_DATA_ROOT / "weights" / "fasterrcnn_resnet50_fpn_v2" / "coco_v1"
    if not any(weights.glob("*.pth")):
        return "model weights are not installed (python -m geopoc.devtools fetch-model ...)"
    return None


_reason = _why_not()
pytestmark = pytest.mark.skipif(_reason is not None, reason=_reason or "")


@pytest.fixture
def real_models(monkeypatch):
    """Point the application at the real manifest and the installed weights, with test stubs switched off."""
    settings = get_settings()
    monkeypatch.setattr(settings, "manifest_path", REPO_ROOT / "models" / "manifest.yaml")
    monkeypatch.setattr(settings, "allow_stub_models", False)
    monkeypatch.setattr(settings, "weights_base", REAL_DATA_ROOT)
    from geopoc.registry.manifest import load_manifest

    entry = load_manifest().get(**MODEL)
    assert entry is not None
    return entry


def test_cuda_runs_a_real_operation_on_this_gpu():
    from geopoc.jobs.probe import capabilities

    info = capabilities()
    assert info["cuda_available"] is True  # set only after a tensor operation returned the right value
    assert "RTX" in info["cuda_device"] or info["cuda_device"]
    assert info["vram_total_mb"] > 0 and info["torch"] and info["torchvision"]

    import torch

    major, minor = torch.cuda.get_device_capability(0)
    assert f"sm_{major}{minor}" in torch.cuda.get_arch_list()  # this build was compiled for this GPU


def test_installed_weights_match_the_manifest_checksum(real_models):
    from geopoc.registry.manifest import weights_problem

    assert weights_problem(real_models, verify_checksum=True) is None
    assert real_models.weights_file().stat().st_size == real_models.weights_size_bytes


def test_model_loads_from_local_weights_with_the_network_unavailable(real_models, monkeypatch):
    from geopoc.pipelines.base import load_adapter

    def no_network(*args, **kwargs):
        raise AssertionError("model loading tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    adapter = load_adapter(real_models)
    info = adapter.load(real_models)
    assert info["device"].startswith("cuda:0")
    assert info["libraries"]["torch"].endswith("+cu128")
    assert len([c for c in adapter.categories if c not in ("__background__", "N/A")]) == 80


def test_inference_path_on_gpu_returns_well_formed_output_within_memory(real_models):
    import numpy as np
    from PIL import Image

    from geopoc.pipelines.base import load_adapter

    adapter = load_adapter(real_models)
    adapter.load(real_models)
    rng = np.random.default_rng(0)
    picture = Image.fromarray(rng.integers(0, 255, (1000, 1333, 3), dtype=np.uint8), "RGB")
    result = adapter.infer(picture)

    assert result.info["inference_ms"] > 0
    assert 0 < result.info["gpu_peak_memory_mb"] < 6000  # well inside 8 GB, with the desktop also using the GPU
    assert result.info["raw_detections"] == len(result.detections) + result.below_min_score
    for detection in result.detections:  # whatever it reports must be well formed
        assert 0.5 <= detection.score <= 1.0 and detection.label in adapter.categories
        x0, y0, x1, y1 = detection.box
        assert 0 <= x0 < x1 <= 1333 and 0 <= y0 < y1 <= 1000


def test_real_model_runs_through_the_job_and_its_output_satisfies_the_contract(
    client, project, headers, run_jobs, real_models
):
    from geopoc.contracts.observation import Observation

    asset = upload(client, project, headers, "photo.jpg", media.image_bytes("JPEG", size=(1600, 1200))).json()["asset"]
    run_jobs()
    job = analyze(client, project, headers, asset["id"], model=MODEL)
    assert job.status_code == 202, job.text
    (status,) = run_jobs()
    assert status in ("needs_review", "completed")  # completed = no findings, valid on a synthetic image

    detail = client.get(f"/api/v1/jobs/{job.json()['id']}").json()
    run = client.get(f"/api/v1/runs/{detail['result']['run_id']}").json()
    assert run["device"].startswith("cuda:0")
    assert run["weights_sha256"] == real_models.weights_sha256
    assert run["library_versions"]["torch"].endswith("+cu128")
    assert run["settings"]["min_score"] == 0.5 and run["summary"]["gpu_peak_memory_mb"] > 0

    for record in observations(client, project, asset_id=asset["id"]):
        observation = Observation.model_validate(record)
        assert observation.review.status.value == "needs_review"
        assert observation.geospatial.geometry is None
        assert observation.claim.category.startswith("coco:")
        assert observation.score.calibrated is False
