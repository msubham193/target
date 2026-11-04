"""General-purpose object detection with a torchvision model, run locally.

PyTorch is imported only here, inside a job's own process, so the API and the worker never load it and GPU
memory is released when the job ends. Weights are read from the local file named in the manifest; nothing is
downloaded at run time.
"""

import time
from typing import Any

from PIL import Image

from geopoc.pipelines.base import Detection, InferenceResult
from geopoc.registry.manifest import ModelEntry

# manifest `settings.architecture` -> (torchvision constructor, weights enum holding the class names)
ARCHITECTURES = {
    "fasterrcnn_resnet50_fpn_v2": ("fasterrcnn_resnet50_fpn_v2", "FasterRCNN_ResNet50_FPN_V2_Weights"),
    "fasterrcnn_mobilenet_v3_large_fpn": (
        "fasterrcnn_mobilenet_v3_large_fpn",
        "FasterRCNN_MobileNet_V3_Large_FPN_Weights",
    ),
}


class TorchvisionDetector:
    def load(self, entry: ModelEntry) -> dict[str, Any]:
        import torch
        import torchvision
        from torchvision.models import detection

        architecture = entry.settings.get("architecture", entry.name)
        if architecture not in ARCHITECTURES:
            raise ValueError(f"unsupported torchvision architecture {architecture!r}")
        constructor, weights_enum = ARCHITECTURES[architecture]
        self.categories: list[str] = getattr(detection, weights_enum).DEFAULT.meta["categories"]
        self.min_score = float(entry.settings.get("min_score", 0.0))
        self.max_detections = int(entry.settings.get("max_detections", 100))

        want = entry.settings.get("device", "auto")
        use_cuda = want in ("auto", "cuda") and torch.cuda.is_available()
        if want == "cuda" and not use_cuda:
            raise RuntimeError("the manifest requires CUDA but it is not available")
        self.device = torch.device("cuda" if use_cuda else "cpu")

        started = time.perf_counter()
        # weights=None and weights_backbone=None: build the bare network, then load the local file. No download.
        model = getattr(detection, constructor)(
            weights=None, weights_backbone=None, box_score_thresh=0.0, box_detections_per_img=300
        )
        state = torch.load(entry.weights_file(), map_location="cpu", weights_only=True)
        model.load_state_dict(state)
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
            if len(kept) < self.max_detections:
                kept.append(Detection(self.categories[label], float(score), tuple(float(v) for v in box)))
        info: dict[str, Any] = {"inference_ms": elapsed, "raw_detections": len(scores)}
        if self.device.type == "cuda":
            info["gpu_peak_memory_mb"] = round(torch.cuda.max_memory_allocated() / 1024**2, 1)
        return InferenceResult(kept, below, info)
