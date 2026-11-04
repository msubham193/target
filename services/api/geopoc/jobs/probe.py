"""Prints what this Python environment can run, as one line of JSON. Loads PyTorch if present; nothing else does
so outside a job."""

import importlib.util
import json
import platform
import shutil


def capabilities() -> dict:
    info: dict = {
        "python": platform.python_version(),
        "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "torch": None,
        "torchvision": None,
        "cuda_available": False,
        "cuda_device": None,
        "cuda_runtime": None,
        "vram_total_mb": None,
    }
    if importlib.util.find_spec("torch") is None:
        return info
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda_runtime"] = torch.version.cuda
        if importlib.util.find_spec("torchvision") is not None:
            import torchvision

            info["torchvision"] = torchvision.__version__
        if torch.cuda.is_available():
            # Availability alone is not enough on new GPUs: run a real operation on the device.
            value = float((torch.ones(8, device="cuda") * 2).sum().item())
            info["cuda_available"] = value == 16.0
            info["cuda_device"] = torch.cuda.get_device_name(0)
            info["vram_total_mb"] = round(torch.cuda.get_device_properties(0).total_memory / 1024**2)
    except Exception as exc:  # e.g. a native library blocked by Windows policy
        info["torch_error"] = f"{type(exc).__name__}: {exc}"[:300]
    return info


if __name__ == "__main__":
    print(json.dumps(capabilities()))
