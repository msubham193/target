"""Object detection with Roboflow's hosted models. ONLINE: the photograph is sent to Roboflow.

These are the detectors that do not run on this machine. They are unavailable unless the owner has switched online mode
on (GEOPOC_ONLINE_ENABLED) and supplied a key (GEOPOC_ROBOFLOW_API_KEY); see `registry.manifest.online_problem`. The
image is re-encoded as JPEG and sent as given to `infer()` (already reduced to the manifest's `max_input_side`), so a
full-size original is never uploaded. The key must never reach a log or an error message: the httpx request logger is
silenced here and every error is re-raised with the key and the URL removed.

Two models share this code:
- `RoboflowDetector`: a trained model from a Roboflow project (`project/version`), with the classes it was trained on.
- `RoboflowWorldDetector`: YOLO-World, an open-vocabulary detector. It finds whatever class names it is given in words,
  with no training, so it suits photographs of anything (ground, drone or satellite). Its scores are less reliable the
  longer the list of names is, so the list is kept short and is set in the manifest or in .env.
"""

import base64
import io
import logging
import time
from typing import Any

import httpx
from PIL import Image

from geopoc.config import get_settings
from geopoc.pipelines.base import Detection, InferenceResult
from geopoc.registry.manifest import ModelEntry

JPEG_QUALITY = 90


class _RoboflowBase:
    provider_label = "online (Roboflow hosted inference)"
    not_found_message = "Roboflow does not know this model."

    def _prepare(self, entry: ModelEntry) -> Any:
        settings = get_settings()
        key = settings.roboflow_api_key.get_secret_value().strip() if settings.roboflow_api_key else ""
        if not settings.online_enabled:
            raise RuntimeError("online mode is off (GEOPOC_ONLINE_ENABLED)")
        if not key:
            raise RuntimeError("no Roboflow API key is set (GEOPOC_ROBOFLOW_API_KEY)")
        # httpx logs every request URL at INFO, and the URL can carry the key.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._key = key
        self._base = settings.roboflow_api_url.rstrip("/")
        self._timeout = settings.roboflow_timeout_s
        self.min_score = float(entry.settings.get("min_score", 0.0))
        self.max_detections = int(entry.settings.get("max_detections", 100))
        return settings

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "***")[:300]

    def _post(self, body: bytes) -> httpx.Response:  # pragma: no cover - implemented by each model
        raise NotImplementedError

    def infer(self, image: Image.Image) -> InferenceResult:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
        body = base64.b64encode(buffer.getvalue())

        started = time.perf_counter()
        try:
            response = self._post(body)
        except httpx.HTTPError as exc:
            # The exception text contains the request URL. Name only its type.
            raise RuntimeError(f"Roboflow could not be reached ({type(exc).__name__}).") from None
        elapsed = round((time.perf_counter() - started) * 1000, 1)

        if response.status_code in (401, 403):
            raise RuntimeError("Roboflow rejected the API key or this model is not available to it.")
        if response.status_code == 404:
            raise RuntimeError(self.not_found_message)
        if response.status_code == 429:
            raise RuntimeError("Roboflow's rate or usage limit was reached. Try again later.")
        if response.status_code != 200:
            raise RuntimeError(f"Roboflow answered HTTP {response.status_code}: {self._redact(response.text)}")
        try:
            predictions = response.json()["predictions"]
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("Roboflow's answer was not in the expected format.") from None

        width, height = image.size
        kept: list[Detection] = []
        for item in predictions:
            try:
                cx, cy, w, h = (float(item[k]) for k in ("x", "y", "width", "height"))
                score = float(item["confidence"])
                label = str(item["class"])
            except (KeyError, TypeError, ValueError):
                continue  # a malformed prediction is skipped, never turned into a finding
            # Roboflow boxes are centre + size; the pipeline wants corners, kept inside the image.
            box = (
                max(0.0, cx - w / 2),
                max(0.0, cy - h / 2),
                min(float(width), cx + w / 2),
                min(float(height), cy + h / 2),
            )
            if score >= self.min_score and box[2] > box[0] and box[3] > box[1] and len(kept) < self.max_detections:
                kept.append(Detection(label, score, box))
        return InferenceResult(
            kept,
            0,
            {"inference_ms": elapsed, "raw_detections": len(predictions), "online": True, "uploaded_bytes": len(body)},
        )


class RoboflowDetector(_RoboflowBase):
    not_found_message = "Roboflow does not know this model id. Check GEOPOC_ROBOFLOW_MODEL_ID."

    def load(self, entry: ModelEntry) -> dict[str, Any]:
        settings = self._prepare(entry)
        model_id = (settings.roboflow_model_id or str(entry.settings.get("model_id") or "")).strip().strip("/")
        if "/" not in model_id:
            raise RuntimeError("the Roboflow model id must look like project/version (GEOPOC_ROBOFLOW_MODEL_ID)")
        self.model_id = model_id
        self._url = f"{self._base}/{model_id}"
        return {
            "device": self.provider_label,
            "online": True,
            "provider": "roboflow",
            "model_id": model_id,
            "libraries": {"httpx": httpx.__version__},
        }

    def _post(self, body: bytes) -> httpx.Response:
        # Roboflow's confidence is a whole percentage; 1 is its lowest useful value. Detections under the manifest's own
        # cutoff are dropped, so the "below cutoff" count is not known for an online model.
        params = {"api_key": self._key, "confidence": max(1, int(self.min_score * 100)), "overlap": 30}
        return httpx.post(
            self._url,
            params=params,
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self._timeout,
        )


def world_classes(entry: ModelEntry, override: str | None) -> list[str]:
    """The class names YOLO-World is asked to find: GEOPOC_ROBOFLOW_WORLD_CLASSES if set, else the manifest's list."""
    if override and override.strip():
        names = [part.strip() for part in override.split(",")]
    else:
        names = [str(name).strip() for name in entry.settings.get("classes") or []]
    seen: dict[str, None] = {}
    for name in names:
        if name:
            seen.setdefault(name, None)
    return list(seen)


class RoboflowWorldDetector(_RoboflowBase):
    not_found_message = "Roboflow's YOLO-World service was not found."

    def load(self, entry: ModelEntry) -> dict[str, Any]:
        settings = self._prepare(entry)
        self.classes = world_classes(entry, settings.roboflow_world_classes)
        if not self.classes:
            raise RuntimeError(
                "no class names are set for YOLO-World (settings.classes or GEOPOC_ROBOFLOW_WORLD_CLASSES)"
            )
        self.version = str(entry.settings.get("yolo_world_version", "v2-l"))
        self._url = f"{self._base}/yolo_world/infer"
        return {
            "device": self.provider_label,
            "online": True,
            "provider": "roboflow",
            "model_id": f"yolo_world/{self.version}",
            "classes": self.classes,
            "libraries": {"httpx": httpx.__version__},
        }

    def _post(self, body: bytes) -> httpx.Response:
        # The key goes in a header, which Roboflow documents as the preferred channel and which keeps it out of the URL.
        return httpx.post(
            self._url,
            headers={"Authorization": f"Bearer {self._key}"},
            json={
                "image": {"type": "base64", "value": body.decode("ascii")},
                "text": self.classes,
                "yolo_world_version_id": self.version,
                "confidence": self.min_score,
            },
            timeout=self._timeout,
        )
