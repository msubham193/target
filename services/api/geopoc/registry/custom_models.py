"""Models uploaded by users through the application ("custom models").

They are kept apart from `models/manifest.yaml`, which belongs to the repository: each uploaded file is stored under
<DATA_ROOT>/models/custom/<id>/ and described in <DATA_ROOT>/models/custom/registry.yaml. Nothing is kept in the
database, so adding or removing a model needs no migration. A pipeline run records the model's name, version and file
checksum as text, exactly as for a model from the manifest.

This module only reads and writes files. Loading a model to check it happens in a separate process
(`geopoc.registry.model_check`), so the API never imports PyTorch or runs code from an uploaded file.
"""

import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from geopoc.config import get_settings

# The kinds of file accepted, by runtime. ONNX and torchvision files hold no code; an Ultralytics .pt file is a Python
# pickle that can run code when it is opened, so it is only accepted when the uploader states that they trust it.
CUSTOM_RUNTIMES = {
    "onnx": "ONNX (YOLOv5/v8/v11/v26 export)",
    "ultralytics": "Ultralytics YOLO .pt",
    "torchvision": "torchvision Faster R-CNN weights",
}
CODE_RUNNING_RUNTIMES = {"ultralytics"}
EXTENSION_RUNTIME = {".onnx": "onnx", ".pt": "ultralytics", ".pth": "torchvision"}
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,62}$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
MAX_CLASSES = 2000
SCORE_SEMANTICS = (
    "The uploaded model's own confidence for the predicted class. A ranking signal from the model, not a calibrated "
    "probability. Nothing about this model has been evaluated by this application."
)

_lock = threading.RLock()


class CustomModelError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CustomModel:
    id: str
    name: str
    version: str
    runtime: str
    file: str  # relative to custom_root()
    sha256: str
    size_bytes: int
    original_filename: str
    added_at: str
    added_by: str | None
    licence: str
    classes: list[str]
    settings: dict[str, Any]

    def absolute_file(self) -> Path:
        return custom_root() / self.file


def custom_root() -> Path:
    return get_settings().data_root / "models" / "custom"


def registry_path() -> Path:
    return custom_root() / "registry.yaml"


def runtime_for(filename: str, requested: str | None) -> str:
    """The runtime for an upload: the one asked for, or the one its extension implies."""
    extension = Path(filename).suffix.lower()
    if requested and requested != "auto":
        if requested not in CUSTOM_RUNTIMES:
            raise CustomModelError("MODEL_FORMAT_UNKNOWN", f"Unknown model format {requested!r}.")
        allowed = {"onnx": {".onnx"}, "ultralytics": {".pt"}, "torchvision": {".pt", ".pth"}}[requested]
        if extension not in allowed:
            raise CustomModelError(
                "UNSUPPORTED_FILE_TYPE",
                f"A {CUSTOM_RUNTIMES[requested]} file must end in {' or '.join(sorted(allowed))}.",
            )
        return requested
    if extension not in EXTENSION_RUNTIME:
        raise CustomModelError(
            "UNSUPPORTED_FILE_TYPE", "A model file must end in .onnx, .pt or .pth; this one does not."
        )
    return EXTENSION_RUNTIME[extension]


def parse_classes(text: str | None) -> list[str]:
    """Class names typed by the uploader: one per line or separated by commas, in the model's class order."""
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"[\n,]", text)]
    names = [part for part in parts if part]
    if len(names) > MAX_CLASSES:
        raise CustomModelError("TOO_MANY_CLASSES", f"At most {MAX_CLASSES} class names are accepted.")
    for item in names:
        if len(item) > 100 or any(ord(c) < 32 for c in item):
            raise CustomModelError(
                "INVALID_CLASS_NAME", "Each class name must be plain text of at most 100 characters."
            )
    return names


def _read() -> list[dict[str, Any]]:
    path = registry_path()
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models = data.get("models") or []
    return [item for item in models if isinstance(item, dict)]


def _write(items: list[dict[str, Any]]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    header = "# Models uploaded through the application. Written by the application: do not edit by hand.\n"
    temp.write_text(header + yaml.safe_dump({"models": items}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    os.replace(temp, path)


def _from_raw(raw: dict[str, Any]) -> CustomModel | None:
    try:
        return CustomModel(
            id=str(raw["id"]),
            name=str(raw["name"]),
            version=str(raw["version"]),
            runtime=str(raw["runtime"]),
            file=str(raw["file"]),
            sha256=str(raw["sha256"]),
            size_bytes=int(raw["size_bytes"]),
            original_filename=str(raw.get("original_filename", "")),
            added_at=str(raw.get("added_at", "")),
            added_by=raw.get("added_by"),
            licence=str(raw.get("licence") or "Not stated by the uploader."),
            classes=[str(c) for c in raw.get("classes") or []],
            settings=dict(raw.get("settings") or {}),
        )
    except (KeyError, TypeError, ValueError):
        return None


def list_models() -> tuple[list[CustomModel], list[str]]:
    """Every uploaded model, and any problems reading the registry (reported, never raised)."""
    with _lock:
        try:
            raws = _read()
        except (OSError, yaml.YAMLError) as exc:
            return [], [f"The uploaded-models list could not be read: {type(exc).__name__}."]
    models, errors = [], []
    for index, raw in enumerate(raws):
        model = _from_raw(raw)
        if model is None or model.runtime not in CUSTOM_RUNTIMES:
            errors.append(f"Uploaded model entry {index} is not valid and was ignored.")
        else:
            models.append(model)
    return models, errors


def find(name: str, version: str) -> CustomModel | None:
    return next((m for m in list_models()[0] if m.name == name and m.version == version), None)


def new_folder() -> tuple[str, Path]:
    model_id = uuid.uuid4().hex
    folder = custom_root() / model_id
    folder.mkdir(parents=True, exist_ok=False)
    return model_id, folder


def remove_folder(model_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", model_id):
        return
    shutil.rmtree(custom_root() / model_id, ignore_errors=True)


def add(
    *,
    model_id: str,
    name: str,
    version: str,
    runtime: str,
    file: Path,
    sha256: str,
    size_bytes: int,
    original_filename: str,
    added_by: str | None,
    licence: str | None,
    classes: list[str],
    settings: dict[str, Any],
    taken: set[tuple[str, str]],
) -> CustomModel:
    """Record a checked model. `taken` is every name@version already in use (manifest and uploads)."""
    with _lock:
        items = _read()
        in_use = taken | {(str(i.get("name")), str(i.get("version"))) for i in items}
        if (name, version) in in_use:
            raise CustomModelError("MODEL_NAME_TAKEN", f"A model called {name} {version} already exists.")
        if any(str(i.get("sha256")) == sha256 for i in items):
            raise CustomModelError("MODEL_DUPLICATE", "This exact model file has already been uploaded.")
        raw = {
            "id": model_id,
            "name": name,
            "version": version,
            "runtime": runtime,
            "file": file.relative_to(custom_root()).as_posix(),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "original_filename": original_filename,
            "added_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "added_by": added_by,
            "licence": licence or "Not stated by the uploader.",
            "classes": classes,
            "settings": settings,
        }
        items.append(raw)
        _write(items)
        model = _from_raw(raw)
        assert model is not None
        return model


def remove(name: str, version: str) -> CustomModel:
    with _lock:
        items = _read()
        keep = [i for i in items if not (str(i.get("name")) == name and str(i.get("version")) == version)]
        gone = [i for i in items if i not in keep]
        model = _from_raw(gone[0]) if gone else None
        if model is None:
            raise CustomModelError("MODEL_NOT_FOUND", "No uploaded model has this name and version.")
        _write(keep)
    remove_folder(model.id)
    return model


def default_settings(runtime: str, min_score: float | None) -> dict[str, Any]:
    return {
        "device": "auto",
        "min_score": min_score if min_score is not None else (0.5 if runtime == "torchvision" else 0.25),
        "max_detections": 300,
        "iou_threshold": 0.45,
        "max_input_side": 1333 if runtime == "torchvision" else 1280,
    }
