"""General-purpose object detection with a torchvision model, run locally.

PyTorch is imported only here, inside a job's own process, so the API and the worker never load it and GPU
memory is released when the job ends. Weights are read from the local file named in the manifest; nothing is
downloaded at run time.

Uploaded weights (a model trained by the user with torchvision) are read with `weights_only=True`, which refuses
anything but tensors, so opening such a file cannot run code. Their architecture is found by trying each supported one;
their class names come from the uploader.
"""

import time
from typing import Any

from PIL import Image

from geopoc.pipelines.base import Detection, InferenceResult
from geopoc.registry.manifest import ModelEntry

# manifest `settings.architecture` -> (torchvision constructor, weights enum holding the class names)
ARCHITECTURES = {
    "fasterrcnn_resnet50_fpn_v2": ("fasterrcnn_resnet50_fpn_v2", "FasterRCNN_ResNet50_FPN_V2_Weights"),
    "fasterrcnn_resnet50_fpn": ("fasterrcnn_resnet50_fpn", "FasterRCNN_ResNet50_FPN_Weights"),
    "fasterrcnn_mobilenet_v3_large_fpn": (
        "fasterrcnn_mobilenet_v3_large_fpn",
        "FasterRCNN_MobileNet_V3_Large_FPN_Weights",
    ),
    "fasterrcnn_mobilenet_v3_large_320_fpn": (
        "fasterrcnn_mobilenet_v3_large_320_fpn",
        "FasterRCNN_MobileNet_V3_Large_320_FPN_Weights",
    ),
}
CLASS_HEAD = "roi_heads.box_predictor.cls_score.weight"
COCO_CLASS_COUNT = 91


def extract_state_dict(loaded: Any) -> dict[str, Any]:
    """The weights in a saved file: a bare state dict, or one inside a training checkpoint."""
    if isinstance(loaded, dict):
        for key in ("model", "state_dict", "model_state_dict", "model_state"):
            if isinstance(loaded.get(key), dict) and CLASS_HEAD in _strip(loaded[key]):
                return _strip(loaded[key])
        state = _strip(loaded)
        if CLASS_HEAD in state:
            return state
    raise ValueError(
        "the file does not hold Faster R-CNN weights (no box predictor was found). Save the trained model with "
        "torch.save(model.state_dict(), 'model.pth')."
    )


def _strip(state: dict[str, Any]) -> dict[str, Any]:
    """Remove the 'module.' prefix that DataParallel training adds."""
    return {(k[7:] if isinstance(k, str) and k.startswith("module.") else k): v for k, v in state.items()}


def category_names(classes: list[str], num_classes: int, coco_categories: list[str]) -> list[str]:
    """The name for each class index the network outputs (index 0 is torchvision's background class)."""
    if classes and len(classes) + 1 == num_classes:
        return ["__background__", *classes]
    if classes and len(classes) == num_classes:
        return list(classes)  # the uploader included the background class
    if not classes and num_classes == COCO_CLASS_COUNT:
        return coco_categories
    raise ValueError(
        f"the weights predict {num_classes} classes including background, so {num_classes - 1} class names are "
        f"needed{f' but {len(classes)} were given' if classes else ''}."
    )


class TorchvisionDetector:
    def load(self, entry: ModelEntry) -> dict[str, Any]:
        import torch
        import torchvision
        from torchvision.models import detection

        self.min_score = float(entry.settings.get("min_score", 0.0))
        self.max_detections = int(entry.settings.get("max_detections", 100))

        want = entry.settings.get("device", "auto")
        use_cuda = want in ("auto", "cuda") and torch.cuda.is_available()
        if want == "cuda" and not use_cuda:
            raise RuntimeError("the manifest requires CUDA but it is not available")
        self.device = torch.device("cuda" if use_cuda else "cpu")

        started = time.perf_counter()
        if entry.custom:
            model = self._load_uploaded(entry, torch, detection)
        else:
            architecture = entry.settings.get("architecture", entry.name)
            if architecture not in ARCHITECTURES:
                raise ValueError(f"unsupported torchvision architecture {architecture!r}")
            constructor, weights_enum = ARCHITECTURES[architecture]
            self.categories: list[str] = getattr(detection, weights_enum).DEFAULT.meta["categories"]
            # weights=None and weights_backbone=None: build the bare network, then load the local file. No download.
            model = getattr(detection, constructor)(
                weights=None, weights_backbone=None, box_score_thresh=0.0, box_detections_per_img=300
            )
            state = torch.load(entry.weights_file(), map_location="cpu", weights_only=True)
            model.load_state_dict(state)
            self.architecture = architecture
        self.model = model.eval().to(self.device)
        self.torch = torch
        if use_cuda:
            torch.cuda.reset_peak_memory_stats()
        return {
            "device": f"cuda:0 ({torch.cuda.get_device_name(0)})" if use_cuda else "cpu",
            "load_ms": round((time.perf_counter() - started) * 1000, 1),
            "libraries": {
                "torch": torch.__version__,
                "torchvision": torchvision.__version__,
                "cuda_runtime": torch.version.cuda,
            },
        }

    def _load_uploaded(self, entry: ModelEntry, torch: Any, detection: Any) -> Any:
        try:
            loaded = torch.load(entry.weights_file(), map_location="cpu", weights_only=True)
        except Exception as exc:
            raise ValueError(
                "the file could not be read as weights only. It may hold a whole pickled model, which is refused "
                "because opening one can run code; save model.state_dict() instead."
            ) from exc
        state = extract_state_dict(loaded)
        num_classes = int(state[CLASS_HEAD].shape[0])
        wanted = entry.settings.get("architecture")
        candidates = [wanted] if wanted in ARCHITECTURES else list(ARCHITECTURES)
        for architecture in candidates:
            constructor, weights_enum = ARCHITECTURES[architecture]
            model = getattr(detection, constructor)(
                weights=None,
                weights_backbone=None,
                num_classes=num_classes,
                box_score_thresh=0.0,
                box_detections_per_img=300,
            )
            try:
                model.load_state_dict(state, strict=True)
            except RuntimeError:
                continue
            coco = getattr(detection, weights_enum).DEFAULT.meta["categories"]
            classes = [str(c) for c in entry.settings.get("classes") or []]
            self.categories = category_names(classes, num_classes, coco)
            self.architecture, self.num_classes = architecture, num_classes
            return model
        raise ValueError(
            "the weights do not match any supported architecture ("
            + ", ".join(ARCHITECTURES)
            + "). Only Faster R-CNN models trained with torchvision can be used."
        )

    def describe(self) -> dict[str, Any]:
        classes = [c for i, c in enumerate(self.categories) if not (i == 0 and c == "__background__")]
        return {
            "classes": classes,
            "settings": {"architecture": self.architecture, "num_classes": getattr(self, "num_classes", None)},
        }

    def infer(self, image: Image.Image) -> InferenceResult:
        from torchvision.transforms.functional import pil_to_tensor

        torch = self.torch
        tensor = pil_to_tensor(image.convert("RGB")).to(self.device).float().div_(255)
        started = time.perf_counter()
        with torch.inference_mode():
            output = self.model([tensor])[0]
        if self.device.type == "cuda":
            torch.cuda.synchronize()
        elapsed = round((time.perf_counter() - started) * 1000, 1)

        boxes = output["boxes"].cpu().tolist()
        labels = output["labels"].cpu().tolist()
        scores = output["scores"].cpu().tolist()
        kept: list[Detection] = []
        below = 0
        for box, label, score in zip(boxes, labels, scores, strict=True):
            if score < self.min_score:
                below += 1
                continue
            if len(kept) < self.max_detections and 0 <= label < len(self.categories):
                kept.append(Detection(self.categories[label], float(score), tuple(float(v) for v in box)))
        info: dict[str, Any] = {"inference_ms": elapsed, "raw_detections": len(scores)}
        if self.device.type == "cuda":
            info["gpu_peak_memory_mb"] = round(torch.cuda.max_memory_allocated() / 1024**2, 1)
        return InferenceResult(kept, below, info)
