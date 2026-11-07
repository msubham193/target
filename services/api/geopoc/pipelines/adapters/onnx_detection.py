"""Object detection with an uploaded ONNX model, run locally with ONNX Runtime.

Accepts the detection (and segmentation, boxes only) exports of YOLOv5, YOLOv8/v11 and the end-to-end exports of
YOLOv10/YOLO26, which is what `yolo export format=onnx` produces. An ONNX file is a graph of operators: opening it
cannot run Python code. Class names are read from the file's metadata when it has them (Ultralytics writes them),
otherwise from the names the uploader typed.

Imported only inside a job's own process (or the upload check's), never by the API or the worker.
"""

import ast
import time
from typing import Any

import numpy as np
from PIL import Image

from geopoc.pipelines.base import Detection, InferenceResult
from geopoc.registry.manifest import ModelEntry

DEFAULT_SIDE = 640
LETTERBOX_GREY = (114, 114, 114)
MASK_COEFFICIENTS = 32  # segmentation exports append 32 mask coefficients per box


def read_names(metadata: dict[str, str]) -> list[str]:
    """Class names from Ultralytics export metadata ("{0: 'person', 1: 'car'}"); [] when absent or unreadable."""
    raw = metadata.get("names")
    if not raw:
        return []
    try:
        value = ast.literal_eval(raw)  # literals only: never evaluates code
    except (ValueError, SyntaxError):
        return []
    if isinstance(value, dict):
        try:
            return [str(value[key]) for key in sorted(value, key=int)]
        except (TypeError, ValueError):
            return []
    if isinstance(value, list | tuple):
        return [str(item) for item in value]
    return []


def input_size(shape: list[Any], metadata: dict[str, str]) -> tuple[int, int]:
    """(width, height) the model expects: fixed in the graph, else the export's imgsz, else 640."""
    if len(shape) == 4 and isinstance(shape[2], int) and isinstance(shape[3], int) and shape[2] > 0 and shape[3] > 0:
        return int(shape[3]), int(shape[2])
    try:
        size = ast.literal_eval(metadata.get("imgsz", ""))
        if isinstance(size, list | tuple) and len(size) == 2:
            return int(size[1]), int(size[0])
        if isinstance(size, int):
            return size, size
    except (ValueError, SyntaxError):
        pass
    return DEFAULT_SIDE, DEFAULT_SIDE


def letterbox(image: Image.Image, size: tuple[int, int]) -> tuple[np.ndarray, float, int, int]:
    """Scale the image into `size` keeping its proportions, pad with grey. Returns NCHW float32 0..1, scale, pads."""
    width, height = image.size
    target_w, target_h = size
    scale = min(target_w / width, target_h / height)
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    canvas = Image.new("RGB", size, LETTERBOX_GREY)
    pad_x, pad_y = (target_w - new_w) // 2, (target_h - new_h) // 2
    canvas.paste(image.convert("RGB").resize((new_w, new_h), Image.Resampling.BILINEAR), (pad_x, pad_y))
    array = np.asarray(canvas, dtype=np.float32) / 255.0
    return array.transpose(2, 0, 1)[None], scale, pad_x, pad_y


def output_layout(shape: tuple[int, ...], class_count: int) -> str:
    """Which YOLO output layout this is: 'end2end' [1,N,6], 'v8' [1,4+nc,N] or 'v5' [1,N,5+nc]."""
    if len(shape) != 3:
        raise ValueError(f"the model's first output has shape {list(shape)}, which is not a YOLO detection output")
    _, rows, cols = shape
    nc = class_count
    if cols == 6 and rows <= 1000 and rows != 4 + nc:
        return "end2end"
    if nc:
        if rows in (4 + nc, 4 + nc + MASK_COEFFICIENTS):
            return "v8"
        if cols in (5 + nc, 5 + nc + MASK_COEFFICIENTS):
            return "v5"
        raise ValueError(
            f"the model's output has shape {list(shape)}, which does not fit {nc} classes. "
            "Check that the class names match the model."
        )
    return "v8" if rows < cols else "v5"


def classes_in_output(shape: tuple[int, ...], layout: str) -> int:
    """How many classes an output carries when the file has no class names (segmentation cannot be told apart)."""
    if layout == "v8":
        return shape[1] - 4
    if layout == "v5":
        return shape[2] - 5
    return 0


def decode(output: np.ndarray, layout: str, class_count: int, floor: float):
    """Boxes (xyxy, model input pixels), scores and class ids of the candidates scoring at least `floor`."""
    if layout == "end2end":
        pred = output[0]
        boxes, scores, ids = pred[:, :4], pred[:, 4], pred[:, 5].astype(np.int64)
    else:
        pred = output[0].T if layout == "v8" else output[0]
        if layout == "v8":
            class_scores = pred[:, 4 : 4 + class_count]
        else:
            class_scores = pred[:, 5 : 5 + class_count] * pred[:, 4:5]
        ids = class_scores.argmax(axis=1)
        scores = class_scores[np.arange(len(class_scores)), ids]
        cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    keep = scores >= floor
    return boxes[keep].astype(np.float32), scores[keep].astype(np.float32), ids[keep]


def nms(boxes: np.ndarray, scores: np.ndarray, ids: np.ndarray, iou_threshold: float) -> np.ndarray:
    """Per-class non-maximum suppression. Returns the indices kept, highest score first."""
    if len(boxes) == 0:
        return np.zeros(0, dtype=np.int64)
    offset = ids.astype(np.float32)[:, None] * (float(boxes.max()) + 1.0)
    shifted = boxes + offset
    x0, y0, x1, y1 = shifted[:, 0], shifted[:, 1], shifted[:, 2], shifted[:, 3]
    areas = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    order = scores.argsort()[::-1]
    kept = []
    while order.size:
        best = order[0]
        kept.append(best)
        rest = order[1:]
        w = np.clip(np.minimum(x1[best], x1[rest]) - np.maximum(x0[best], x0[rest]), 0, None)
        h = np.clip(np.minimum(y1[best], y1[rest]) - np.maximum(y0[best], y0[rest]), 0, None)
        inter = w * h
        iou = inter / np.maximum(areas[best] + areas[rest] - inter, 1e-9)
        order = rest[iou <= iou_threshold]
    return np.asarray(kept, dtype=np.int64)


def postprocess(
    output: np.ndarray,
    *,
    layout: str,
    classes: list[str],
    scale: float,
    pad: tuple[int, int],
    image_size: tuple[int, int],
    min_score: float,
    iou_threshold: float,
    max_detections: int,
) -> tuple[list[Detection], int]:
    """Detections in the pixels of the image given to infer(), and how many scored under the cutoff."""
    floor = min(min_score, 0.1)
    boxes, scores, ids = decode(output, layout, len(classes), floor)
    if layout != "end2end":
        order = nms(boxes, scores, ids, iou_threshold)
        boxes, scores, ids = boxes[order], scores[order], ids[order]
    width, height = image_size
    kept: list[Detection] = []
    below = 0
    for box, score, class_id in zip(boxes, scores, ids, strict=True):
        if score < min_score:
            below += 1
            continue
        if len(kept) >= max_detections or not 0 <= int(class_id) < len(classes):
            continue
        x0 = float(np.clip((box[0] - pad[0]) / scale, 0, width))
        y0 = float(np.clip((box[1] - pad[1]) / scale, 0, height))
        x1 = float(np.clip((box[2] - pad[0]) / scale, 0, width))
        y1 = float(np.clip((box[3] - pad[1]) / scale, 0, height))
        if x1 > x0 and y1 > y0:
            kept.append(Detection(classes[int(class_id)], float(score), (x0, y0, x1, y1)))
    return kept, below


class OnnxDetector:
    def load(self, entry: ModelEntry) -> dict[str, Any]:
        import onnxruntime as ort

        settings = entry.settings
        self.min_score = float(settings.get("min_score", 0.25))
        self.iou_threshold = float(settings.get("iou_threshold", 0.45))
        self.max_detections = int(settings.get("max_detections", 300))
        want = settings.get("device", "auto")
        self.ort = ort
        self.path = str(entry.weights_file())
        cuda = want in ("auto", "cuda") and "CUDAExecutionProvider" in ort.get_available_providers()
        if want == "cuda" and not cuda:
            raise RuntimeError("the model requires CUDA but ONNX Runtime cannot use it here")
        if cuda:
            _make_cuda_libraries_findable(ort)
        started = time.perf_counter()
        self._open(["CUDAExecutionProvider", "CPUExecutionProvider"] if cuda else ["CPUExecutionProvider"])

        meta = self.session.get_modelmeta().custom_metadata_map or {}
        model_input = self.session.get_inputs()[0]
        self.input_name = model_input.name
        self.input_half = model_input.type == "tensor(float16)"
        self.size = input_size(list(model_input.shape), meta)
        output_shape = self._probe_output_shape()
        file_names = read_names(meta)
        typed = [str(c) for c in settings.get("classes") or []]
        if typed and file_names and len(typed) != len(file_names):
            raise ValueError(f"the file lists {len(file_names)} classes but {len(typed)} class names were given")
        self.classes = typed or file_names
        self.layout = output_layout(output_shape, len(self.classes))
        if not self.classes:
            raise ValueError(
                "the file does not contain class names. Type them in the model's class order "
                f"(it predicts {classes_in_output(output_shape, self.layout)} classes)."
            )
        self.task = meta.get("task", "detect")
        return {
            "device": self._device(),
            "load_ms": round((time.perf_counter() - started) * 1000, 1),
            "libraries": {"onnxruntime": ort.__version__},
        }

    def _open(self, providers: list[str]) -> None:
        options = self.ort.SessionOptions()
        options.log_severity_level = 3
        self.session = self.ort.InferenceSession(self.path, sess_options=options, providers=providers)

    def _device(self) -> str:
        return (
            "cuda:0 (ONNX Runtime)" if "CUDAExecutionProvider" in self.session.get_providers() else "cpu (ONNX Runtime)"
        )

    def _probe_output_shape(self) -> tuple[int, ...]:
        shape = self.session.get_outputs()[0].shape
        if all(isinstance(d, int) and d > 0 for d in shape):
            return tuple(shape)
        blank = np.zeros((1, 3, self.size[1], self.size[0]), dtype=np.float16 if self.input_half else np.float32)
        return tuple(self._run(blank).shape)

    def _run(self, tensor: np.ndarray) -> np.ndarray:
        try:
            return self.session.run(None, {self.input_name: tensor})[0]
        except Exception:
            if "CUDAExecutionProvider" not in self.session.get_providers():
                raise
            # e.g. a GPU this ONNX Runtime build has no kernels for: run on the CPU instead, and say so in the run
            self._open(["CPUExecutionProvider"])
            self.fell_back_to_cpu = True
            return self.session.run(None, {self.input_name: tensor})[0]

    def describe(self) -> dict[str, Any]:
        return {
            "classes": self.classes,
            "settings": {"input_size": list(self.size), "output_layout": self.layout, "task": self.task},
        }

    def infer(self, image: Image.Image) -> InferenceResult:
        tensor, scale, pad_x, pad_y = letterbox(image, self.size)
        if self.input_half:
            tensor = tensor.astype(np.float16)
        started = time.perf_counter()
        output = self._run(tensor).astype(np.float32)
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        detections, below = postprocess(
            output,
            layout=self.layout,
            classes=self.classes,
            scale=scale,
            pad=(pad_x, pad_y),
            image_size=image.size,
            min_score=self.min_score,
            iou_threshold=self.iou_threshold,
            max_detections=self.max_detections,
        )
        info: dict[str, Any] = {"inference_ms": elapsed, "device_used": self._device(), "input_size": list(self.size)}
        if getattr(self, "fell_back_to_cpu", False):
            info["note"] = "The GPU could not run this model, so it ran on the CPU."
        return InferenceResult(detections, below, info)


def _make_cuda_libraries_findable(ort: Any) -> None:
    """ONNX Runtime's CUDA provider needs the CUDA and cuDNN libraries; PyTorch's CUDA build ships them."""
    try:
        import torch  # noqa: F401  (loading it puts its CUDA libraries in the process)
    except Exception:
        pass
    preload = getattr(ort, "preload_dlls", None)
    if preload is not None:
        try:
            preload()
        except Exception:
            pass
