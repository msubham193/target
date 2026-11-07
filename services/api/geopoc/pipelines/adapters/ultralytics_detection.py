"""Object detection with an uploaded Ultralytics YOLO .pt file, run locally.

A .pt file is a Python pickle: opening it can run code written into it. Such a file is only accepted when the
uploader has stated that they trust its source, and it is only ever opened inside a job's own process (or the upload
check's), never by the API or the worker. Ultralytics is kept offline: no update checks, no downloads and no usage
reporting.
"""

import os
import time
from typing import Any

from PIL import Image

from geopoc.pipelines.base import Detection, InferenceResult
from geopoc.registry.manifest import ModelEntry

USABLE_TASKS = {"detect", "segment", "obb", "pose"}  # tasks that output boxes; "classify" does not


def prepare_environment() -> None:
    """Keep Ultralytics offline and its settings file inside the data folder. Call before importing it."""
    from geopoc.config import get_settings

    os.environ["YOLO_OFFLINE"] = "1"
    os.environ.setdefault("YOLO_VERBOSE", "False")
    os.environ.setdefault("YOLO_CONFIG_DIR", str(get_settings().data_root / "models" / "ultralytics-config"))


def _no_usage_reporting() -> None:
    try:
        from ultralytics.utils import SETTINGS

        if SETTINGS.get("sync", False):
            SETTINGS.update({"sync": False})
    except Exception:
        pass


class UltralyticsDetector:
    def load(self, entry: ModelEntry) -> dict[str, Any]:
        prepare_environment()
        import torch
        import ultralytics
        from ultralytics import YOLO

        _no_usage_reporting()
        settings = entry.settings
        self.min_score = float(settings.get("min_score", 0.25))
        self.iou_threshold = float(settings.get("iou_threshold", 0.45))
        self.max_detections = int(settings.get("max_detections", 300))
        self.imgsz = settings.get("imgsz")
        want = settings.get("device", "auto")
        use_cuda = want in ("auto", "cuda") and torch.cuda.is_available()
        if want == "cuda" and not use_cuda:
            raise RuntimeError("the model requires CUDA but it is not available")
        self.device = "cuda:0" if use_cuda else "cpu"

        started = time.perf_counter()
        self.model = YOLO(str(entry.weights_file()))
        self.task = str(self.model.task)
        if self.task not in USABLE_TASKS:
            raise ValueError(
                f"this is a {self.task} model. It does not mark objects with boxes, so it cannot be used for detection."
            )
        names = self.model.names
        file_names = [str(names[key]) for key in sorted(names)] if isinstance(names, dict) else [str(n) for n in names]
        typed = [str(c) for c in settings.get("classes") or []]
        if typed and len(typed) != len(file_names):
            raise ValueError(f"the file lists {len(file_names)} classes but {len(typed)} class names were given")
        self.classes = typed or file_names
        if not self.imgsz:
            self.imgsz = int((getattr(self.model, "overrides", {}) or {}).get("imgsz") or 640)
        self.torch = torch
        return {
            "device": f"cuda:0 ({torch.cuda.get_device_name(0)})" if use_cuda else "cpu",
            "load_ms": round((time.perf_counter() - started) * 1000, 1),
            "libraries": {"ultralytics": ultralytics.__version__, "torch": torch.__version__},
        }

    def describe(self) -> dict[str, Any]:
        return {"classes": self.classes, "settings": {"task": self.task, "imgsz": self.imgsz}}

    def infer(self, image: Image.Image) -> InferenceResult:
        started = time.perf_counter()
        result = self.model.predict(
            source=image.convert("RGB"),
            conf=self.min_score,
            iou=self.iou_threshold,
            max_det=self.max_detections,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )[0]
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        found = result.boxes if result.boxes is not None else getattr(result, "obb", None)
        kept: list[Detection] = []
        if found is not None and len(found):
            boxes = found.xyxy.cpu().tolist()
            scores = found.conf.cpu().tolist()
            ids = found.cls.cpu().tolist()
            for box, score, class_id in zip(boxes, scores, ids, strict=True):
                index = int(class_id)
                if 0 <= index < len(self.classes):
                    kept.append(Detection(self.classes[index], float(score), tuple(float(v) for v in box)))
        info: dict[str, Any] = {
            "inference_ms": elapsed,
            "imgsz": self.imgsz,
            "note": "Ultralytics applies the cutoff itself, so detections below it are not counted.",
        }
        return InferenceResult(kept, 0, info)
