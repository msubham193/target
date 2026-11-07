"""Checks an uploaded model file before it is accepted: `python -m geopoc.registry.model_check '<json>'`.

Runs in its own short-lived process (started by the API), so the API never imports PyTorch, ONNX Runtime or
Ultralytics, and never opens an uploaded file itself. It loads the file with the adapter that will run it, runs it once
on a blank image, and prints one line of JSON: the class names and settings it found, or why it could not be used.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def check(runtime: str, path: str, settings: dict[str, Any]) -> dict[str, Any]:
    from geopoc.pipelines.base import load_adapter
    from geopoc.registry.manifest import ModelEntry

    entry = ModelEntry(
        name="upload-check",
        version="0",
        pipeline="ground_image",
        task="object_detection",
        runtime=runtime,
        licence="",
        score_semantics="",
        score_calibrated=False,
        score_range=(0.0, 1.0),
        settings=settings,
        weights_path=path,
        custom=True,
    )
    adapter = load_adapter(entry)
    info = adapter.load(entry)
    result = adapter.infer(Image.new("RGB", (640, 480), (128, 128, 128)))
    described = adapter.describe()
    return {
        "ok": True,
        "classes": described["classes"],
        "settings": described["settings"],
        "device": info.get("device"),
        "libraries": info.get("libraries", {}),
        "test_detections": len(result.detections),
    }


def run_check(runtime: str, path: Path, settings: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    """Run the check in a child process. Never raises: a failure is returned as {"ok": False, "error": ...}."""
    request = json.dumps({"runtime": runtime, "path": str(path), "settings": settings})
    try:
        out = subprocess.run(
            [sys.executable, "-m", "geopoc.registry.model_check", request],
            capture_output=True,
            timeout=timeout_s,
            creationflags=_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Checking the model took longer than {timeout_s:.0f} seconds."}
    lines = out.stdout.decode("utf-8", errors="replace").strip().splitlines()
    for line in reversed(lines):
        try:
            answer = json.loads(line)
        except ValueError:
            continue
        if isinstance(answer, dict) and "ok" in answer:
            return answer
    return {"ok": False, "error": "The model check stopped without an answer (the model may have crashed it)."}


def _main() -> None:
    try:
        request = json.loads(sys.argv[1])
        answer = check(str(request["runtime"]), str(request["path"]), dict(request.get("settings") or {}))
    except ImportError as exc:
        answer = {"ok": False, "error": f"The runtime this model needs is not installed: {exc}"}
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        answer = {"ok": False, "error": message[:600]}
    sys.stdout.write("\n" + json.dumps(answer) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    _main()
