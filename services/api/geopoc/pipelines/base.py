"""What a detection model must provide to a pipeline. Pipelines know this interface, not any particular model."""

from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image

from geopoc.registry.manifest import ModelEntry


@dataclass(frozen=True)
class Detection:
    label: str  # the model's own class name
    score: float  # the model's own score, unchanged
    box: tuple[float, float, float, float]  # x_min, y_min, x_max, y_max in pixels of the image given to infer()


@dataclass
class InferenceResult:
    detections: list[Detection]
    below_min_score: int  # raw detections the model produced under the configured cutoff
    info: dict[str, Any]  # device, timings, memory: recorded on the run


class DetectorAdapter(Protocol):
    def load(self, entry: ModelEntry) -> dict[str, Any]:
        """Load the model. Returns facts about the load (device, library versions)."""

    def infer(self, image: Image.Image) -> InferenceResult: ...


def load_adapter(entry: ModelEntry) -> DetectorAdapter:
    if entry.runtime == "stub":
        from geopoc.pipelines.adapters.stub import StubDetector

        return StubDetector()
    if entry.runtime == "torchvision":
        from geopoc.pipelines.adapters.torchvision_detection import TorchvisionDetector

        return TorchvisionDetector()
    if entry.runtime == "roboflow":
        from geopoc.pipelines.adapters.roboflow import RoboflowDetector

        return RoboflowDetector()
    if entry.runtime == "roboflow_world":
        from geopoc.pipelines.adapters.roboflow import RoboflowWorldDetector

        return RoboflowWorldDetector()
    if entry.runtime == "onnx":
        from geopoc.pipelines.adapters.onnx_detection import OnnxDetector

        return OnnxDetector()
    if entry.runtime == "ultralytics":
        from geopoc.pipelines.adapters.ultralytics_detection import UltralyticsDetector

        return UltralyticsDetector()
    raise ValueError(f"no adapter for runtime {entry.runtime!r}")
