"""A detector for automated tests only. It returns the fixed boxes written in its manifest entry.

It is refused by the manifest loader unless GEOPOC_ALLOW_STUB_MODELS is set, so it can never appear among the
models of a working installation or produce made-up findings in a real project.
"""

import time
from typing import Any

from PIL import Image

from geopoc.pipelines.base import Detection, InferenceResult
from geopoc.registry.manifest import ModelEntry


class StubDetector:
    def load(self, entry: ModelEntry) -> dict[str, Any]:
        self.settings = entry.settings
        return {"device": "none (test stub)", "libraries": {}}

    def infer(self, image: Image.Image) -> InferenceResult:
        if self.settings.get("sleep_s"):
            time.sleep(float(self.settings["sleep_s"]))
        if self.settings.get("fail"):
            raise RuntimeError("stub model failure requested by the test")
        width, height = image.size
        cutoff = float(self.settings.get("min_score", 0.0))
        kept, below = [], 0
        for item in self.settings.get("detections", []):
            if item["score"] < cutoff:
                below += 1
                continue
            x0, y0, x1, y1 = item["box_fraction"]
            kept.append(
                Detection(item["label"], float(item["score"]), (x0 * width, y0 * height, x1 * width, y1 * height))
            )
        return InferenceResult(kept, below, {"inference_ms": 0.0})
