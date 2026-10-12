"""Model manifest reader. Phase 1 only reports what the manifest says; it never loads or contacts a model."""

from pathlib import Path
from typing import Any

import yaml

NOT_CHECKED = "Availability checks (weights, checksum, runtime, device) are implemented in Phase 3."


def read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"manifest_found": False, "models": [], "errors": ["Model manifest file not found."]}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = data.get("models") or []
        if not isinstance(entries, list):
            raise ValueError("'models' must be a list")
    except (yaml.YAMLError, ValueError, AttributeError) as exc:
        return {"manifest_found": True, "models": [], "errors": [f"Model manifest is not valid: {exc}"]}

    models: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("name"):
            errors.append(f"Entry {index} is not a valid model definition.")
            continue
        if entry.get("locality", "local") != "local":
            # Imagery and project data must never be sent to a remote model.
            errors.append(f"Model {entry['name']!r} is not local and was refused.")
            continue
        models.append(
            {
                "name": str(entry["name"]),
                "version": str(entry.get("version", "")),
                "pipeline": entry.get("pipeline"),
                "runtime": entry.get("runtime"),
                "available": False,
                "reason_unavailable": NOT_CHECKED,
            }
        )
    return {"manifest_found": True, "models": models, "errors": errors}
