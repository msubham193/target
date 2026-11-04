"""Model manifest: which models exist, what they are, and whether their files are present.

Model configuration lives in `models/manifest.yaml`, never in pipeline code. This module only reads files and
checks them; it never loads a model, imports PyTorch or contacts anything.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from geopoc.config import get_settings

RUNTIMES = {"torchvision", "stub", "roboflow", "roboflow_world"}
# Runtimes that send the photo to a third party. Only these may be declared `locality: remote`; every other model must
# run on this machine. They stay unavailable until the account owner switches online mode on (GEOPOC_ONLINE_ENABLED).
REMOTE_RUNTIMES = {"roboflow", "roboflow_world"}


@dataclass(frozen=True)
class ModelEntry:
    name: str
    version: str
    pipeline: str
    task: str
    runtime: str
    licence: str
    score_semantics: str
    score_calibrated: bool
    score_range: tuple[float, float] | None
    settings: dict[str, Any] = field(default_factory=dict)
    weights_path: str | None = None  # relative to DATA_ROOT
    weights_sha256: str | None = None
    weights_size_bytes: int | None = None
    weights_source_url: str | None = None
    card: str | None = None
    locality: str = "local"  # "remote" means the photo is sent to a third party

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def weights_file(self) -> Path | None:
        settings = get_settings()
        return (settings.weights_base or settings.data_root) / self.weights_path if self.weights_path else None


@dataclass
class Manifest:
    found: bool
    models: list[ModelEntry]
    errors: list[str]

    def get(self, name: str, version: str) -> ModelEntry | None:
        return next((m for m in self.models if m.name == name and m.version == version), None)


def _parse(index: int, raw: Any, allow_stub: bool) -> ModelEntry | str:
    if not isinstance(raw, dict) or not raw.get("name"):
        return f"Entry {index} is not a valid model definition."
    name = str(raw["name"])
    locality = str(raw.get("locality", "local"))
    runtime = str(raw.get("runtime", ""))
    if locality != "local" and not (locality == "remote" and runtime in REMOTE_RUNTIMES):
        # Imagery and project data must never be sent to a remote model, except through a runtime built for it.
        return f"Model {name!r} is not local and was refused."
    if runtime in REMOTE_RUNTIMES and locality != "remote":
        return f"Model {name!r} sends photos to a third party and must be declared 'locality: remote'."
    if runtime not in RUNTIMES:
        return f"Model {name!r} names an unknown runtime {runtime!r}."
    if runtime == "stub" and not allow_stub:
        return f"Model {name!r} is a test stub and was refused."
    score = raw.get("score") or {}
    if not score.get("semantics"):
        return f"Model {name!r} does not state what its score means."
    weights = raw.get("weights") or {}
    value_range = score.get("range")
    return ModelEntry(
        name=name,
        version=str(raw.get("version", "")),
        pipeline=str(raw.get("pipeline", "")),
        task=str(raw.get("task", "")),
        runtime=runtime,
        licence=str(raw.get("licence", "not stated")),
        score_semantics=str(score["semantics"]),
        score_calibrated=bool(score.get("calibrated", False)),
        score_range=(float(value_range[0]), float(value_range[1])) if value_range else None,
        settings=dict(raw.get("settings") or {}),
        weights_path=weights.get("path"),
        weights_sha256=weights.get("sha256"),
        weights_size_bytes=weights.get("size_bytes"),
        weights_source_url=weights.get("source_url"),
        card=raw.get("card"),
        locality=locality,
    )


def load_manifest() -> Manifest:
    settings = get_settings()
    path = settings.manifest_path
    if not path.is_file():
        return Manifest(False, [], ["Model manifest file not found."])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = data.get("models") or []
        if not isinstance(entries, list):
            raise ValueError("'models' must be a list")
    except (yaml.YAMLError, ValueError, AttributeError) as exc:
        return Manifest(True, [], [f"Model manifest is not valid: {exc}"])

    models: list[ModelEntry] = []
    errors: list[str] = []
    for index, raw in enumerate(entries):
        parsed = _parse(index, raw, settings.allow_stub_models)
        if isinstance(parsed, str):
            errors.append(parsed)
        else:
            models.append(parsed)
    return Manifest(True, models, errors)


@lru_cache(maxsize=8)
def _file_sha256(path: str, size: int, mtime_ns: int) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def online_problem(entry: ModelEntry) -> str | None:
    """Why an online model cannot be used yet, or None. It has no weights file; it needs the owner opt-in and a key."""
    settings = get_settings()
    if not settings.online_enabled:
        return (
            "Online detection is switched off. Photos are sent to a third party by this model, so it needs "
            "GEOPOC_ONLINE_ENABLED=true in .env."
        )
    key = settings.roboflow_api_key
    if key is None or not key.get_secret_value().strip():
        return "No Roboflow API key is set. Add GEOPOC_ROBOFLOW_API_KEY to .env."
    if entry.runtime == "roboflow" and not (settings.roboflow_model_id or entry.settings.get("model_id")):
        return "No Roboflow model is chosen. Set GEOPOC_ROBOFLOW_MODEL_ID (project/version) in .env."
    return None


def weights_problem(entry: ModelEntry, verify_checksum: bool = False) -> str | None:
    """Why this model's files cannot be used, or None. The checksum is verified before a model is loaded."""
    if entry.runtime == "stub":
        return None
    if entry.runtime in REMOTE_RUNTIMES:
        return online_problem(entry)
    file = entry.weights_file()
    if file is None or not entry.weights_sha256:
        return "The manifest does not give this model's weights file and checksum."
    if not file.is_file():
        return "The model weights are not installed on this machine."
    stat = file.stat()
    if entry.weights_size_bytes is not None and stat.st_size != entry.weights_size_bytes:
        return "The installed weights file has the wrong size."
    if verify_checksum and _file_sha256(str(file), stat.st_size, stat.st_mtime_ns) != entry.weights_sha256:
        return "The installed weights file does not match the checksum in the manifest."
    return None


def runtime_problem(entry: ModelEntry, capabilities: dict[str, Any] | None) -> str | None:
    """Why the worker cannot run this model, judged from what the worker last reported about itself."""
    if entry.runtime == "stub" or entry.runtime in REMOTE_RUNTIMES:
        return None  # an online model needs no PyTorch; the worker only needs a network connection
    if capabilities is None:
        return "No worker is running, so it is not known whether this model can be run."
    if capabilities.get("probe") == "running":
        return "The worker has just started and is still checking whether PyTorch is available. Try again shortly."
    if not capabilities.get("torch") or not capabilities.get("torchvision"):
        return (
            capabilities.get("torch_error") or "PyTorch and torchvision are not installed in the worker's environment."
        )
    return None
