"""SAM 2.1 compatibility test: person-guided outlining on a synthetic image, in the isolated evaluation environment.

Run only after approval and setup_env.ps1, with the evaluation interpreter:

    D:\\geopoc-cache\\venv-sam2-eval\\Scripts\\python.exe experiments\\sam2_compat\\compat_test.py --point 380,300
    D:\\geopoc-cache\\venv-sam2-eval\\Scripts\\python.exe experiments\\sam2_compat\\compat_test.py --box 250,170,510,430

It measures loading, first and later inference time, peak GPU memory, repeatability, invalid prompts and empty
outputs, and whether anything tries to use the network. It never touches the application, its database or its data
folder; results go to D:\\geopoc-cache\\sam2-eval\\results. Synthetic inputs say nothing about accuracy, and this
script makes no accuracy claim.
"""

import argparse
import hashlib
import json
import os
import platform
import socket
import sys
import time
import traceback
import warnings
from datetime import UTC, datetime
from pathlib import Path

# --- 1. no network: installed before anything else is imported -------------------------------------------------------
NETWORK_ATTEMPTS: list[str] = []


def _refuse(*args, **kwargs):
    NETWORK_ATTEMPTS.append(repr(args[:2])[:200])
    raise OSError("network access is disabled for this experiment")


socket.socket.connect = _refuse  # type: ignore[method-assign]
socket.socket.connect_ex = _refuse  # type: ignore[method-assign]
socket.create_connection = _refuse  # type: ignore[assignment]
socket.getaddrinfo = _refuse  # type: ignore[assignment]
os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PROJ_NETWORK": "OFF"})

ROOT = Path(r"D:\geopoc-cache\sam2-eval")
CHECKPOINTS = {
    "tiny": ("sam2.1_hiera_tiny.pt", "configs/sam2.1/sam2.1_hiera_t.yaml"),
    "small": ("sam2.1_hiera_small.pt", "configs/sam2.1/sam2.1_hiera_s.yaml"),
}
APP_VENV = Path(r"D:\geopoc-cache\venv-api")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def synthetic_image(path: Path) -> None:
    """A fixed 768 x 576 RGB test image: a bright disk and a darker rectangle on a smooth, noisy background."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(20261021)
    h, w = 576, 768
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.stack([60 + xx / w * 40, 70 + yy / h * 40, np.full((h, w), 80.0)], axis=-1)
    img += rng.normal(0, 6, img.shape)
    disk = (xx - 380) ** 2 + (yy - 300) ** 2 < 110**2
    img[disk] = [220, 200, 60]
    rect = (xx > 560) & (xx < 700) & (yy > 80) & (yy < 200)
    img[rect] = [30, 40, 150]
    Image.fromarray(np.clip(img, 0, 255).astype("uint8")).save(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", choices=sorted(CHECKPOINTS), default="tiny")
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--point", help="x,y in pixels: a positive point placed by the person running the test")
    prompt.add_argument("--box", help="x0,y0,x1,y1 in pixels")
    parser.add_argument("--image", type=Path, help="defaults to a generated synthetic image")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()

    if Path(sys.prefix).resolve() == APP_VENV.resolve():
        print("Refusing to run inside the application environment.", file=sys.stderr)
        return 2
    import numpy as np
    import torch
    from PIL import Image

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "results" / f"{stamp}-{args.checkpoint}"
    out.mkdir(parents=True, exist_ok=True)
    image_path = args.image or out / "synthetic.png"
    if args.image is None:
        synthetic_image(image_path)
    before = sha256(image_path)
    image = np.array(Image.open(image_path).convert("RGB"))
    h, w = image.shape[:2]

    report: dict = {
        "started_utc": stamp,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "device": args.device,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "checkpoint": args.checkpoint,
        "image": {"path": str(image_path), "size": [w, h], "sha256_before": before, "synthetic": args.image is None},
        "prompt": {"point": args.point, "box": args.box, "placed_by": "the person running the test"},
        "warnings": [],
        "steps": {},
    }
    caught = warnings.catch_warnings(record=True)
    recorded = caught.__enter__()
    warnings.simplefilter("always")

    def step(name, fn):
        started = time.perf_counter()
        try:
            value = fn()
            if args.device == "cuda":
                torch.cuda.synchronize()
            report["steps"][name] = {"ok": True, "seconds": round(time.perf_counter() - started, 4)}
            return value
        except Exception as exc:  # noqa: BLE001 - every failure is the result being measured
            report["steps"][name] = {
                "ok": False,
                "seconds": round(time.perf_counter() - started, 4),
                "error": f"{type(exc).__name__}: {exc}"[:2000],
                "traceback": traceback.format_exc()[-4000:],
            }
            return None

    filename, config = CHECKPOINTS[args.checkpoint]
    checkpoint = ROOT / "checkpoints" / filename
    report["checkpoint_sha256"] = sha256(checkpoint)
    if args.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
        report["gpu_free_mib_before"] = torch.cuda.mem_get_info()[0] // 2**20

    def load():
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        return SAM2ImagePredictor(build_sam2(config, str(checkpoint), device=args.device))

    predictor = step("import_and_load", load)
    if predictor is None:
        return finish(report, recorded, caught, out, image_path, before)

    def parse(text):
        return [float(v) for v in text.split(",")]

    def predict(**kwargs):
        with torch.inference_mode():
            return predictor.predict(multimask_output=True, **kwargs)

    user = (
        {"point_coords": np.array([parse(args.point)]), "point_labels": np.array([1])}
        if args.point
        else {"box": np.array(parse(args.box))}
    )

    # --- timing: first run, then repeats; float32 as loaded ---
    with torch.inference_mode():
        step("set_image_first", lambda: predictor.set_image(image))
    first = step("predict_first", lambda: predict(**user))
    runs = []
    for i in range(args.repeats):
        with torch.inference_mode():
            step(f"set_image_repeat_{i}", lambda: predictor.set_image(image))
        runs.append(step(f"predict_repeat_{i}", lambda: predict(**user)))
    if args.device == "cuda":
        report["gpu_peak_allocated_mib"] = round(torch.cuda.max_memory_allocated() / 2**20, 1)
        report["gpu_peak_reserved_mib"] = round(torch.cuda.max_memory_reserved() / 2**20, 1)

    # --- repeatability ---
    if first is not None and all(r is not None for r in runs):
        masks0, scores0, logits0 = first
        report["result"] = {
            "mask_shape": list(masks0.shape),
            "scores": [round(float(s), 6) for s in scores0],
            "mask_area_px": [int(m.sum()) for m in masks0],
        }
        report["repeatability"] = [
            {
                "masks_identical": bool(np.array_equal(masks0, m)),
                "iou_best_mask": float(
                    np.logical_and(masks0[scores0.argmax()], m[s.argmax()]).sum()
                    / max(1, np.logical_or(masks0[scores0.argmax()], m[s.argmax()]).sum())
                ),
                "max_abs_logit_diff": float(np.abs(logits0 - lg).max()),
            }
            for m, s, lg in runs
        ]
        best = masks0[scores0.argmax()].astype("uint8") * 255
        Image.fromarray(best).save(out / "best_mask.png")

    # --- bfloat16 autocast, as Meta's example runs it (reported separately) ---
    if args.device == "cuda":

        def bf16():
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                predictor.set_image(image)
                return predictor.predict(multimask_output=True, **user)

        step("bfloat16_first", bf16)
        step("bfloat16_second", bf16)

    # --- invalid prompts and empty outputs: what the model does, so the application can validate before calling it ---
    with torch.inference_mode():
        predictor.set_image(image)
    cases = {
        "point_outside_image": {"point_coords": np.array([[w + 500.0, h + 500.0]]), "point_labels": np.array([1])},
        "negative_point_only": {
            "point_coords": np.array([parse(args.point or "380,300")]),
            "point_labels": np.array([0]),
        },
        "nan_point": {"point_coords": np.array([[float("nan"), 10.0]]), "point_labels": np.array([1])},
        "label_not_0_or_1": {"point_coords": np.array([[10.0, 10.0]]), "point_labels": np.array([7])},
        "inverted_box": {"box": np.array([500.0, 400.0, 100.0, 50.0])},
        "zero_area_box": {"box": np.array([100.0, 100.0, 100.0, 100.0])},
        "no_prompt": {},
        "uniform_background_point": {"point_coords": np.array([[20.0, 560.0]]), "point_labels": np.array([1])},
    }
    report["prompt_cases"] = {}
    for name, kwargs in cases.items():
        try:
            masks, scores, _ = predict(**kwargs)
            report["prompt_cases"][name] = {
                "raised": None,
                "scores": [round(float(s), 6) for s in scores],
                "mask_area_px": [int(m.sum()) for m in masks],
                "empty_masks": int(sum(int(m.sum()) == 0 for m in masks)),
            }
        except Exception as exc:  # noqa: BLE001
            report["prompt_cases"][name] = {"raised": f"{type(exc).__name__}: {exc}"[:500]}
    blank = np.zeros_like(image)
    try:
        with torch.inference_mode():
            predictor.set_image(blank)
        masks, scores, _ = predict(**user)
        report["prompt_cases"]["all_black_image"] = {
            "raised": None,
            "scores": [round(float(s), 6) for s in scores],
            "mask_area_px": [int(m.sum()) for m in masks],
        }
    except Exception as exc:  # noqa: BLE001
        report["prompt_cases"]["all_black_image"] = {"raised": f"{type(exc).__name__}: {exc}"[:500]}
    return finish(report, recorded, caught, out, image_path, before)


def finish(report, recorded, caught, out, image_path, before) -> int:
    caught.__exit__(None, None, None)
    report["warnings"] = sorted({f"{w.category.__name__}: {str(w.message)[:300]}" for w in recorded})
    report["network_attempts"] = NETWORK_ATTEMPTS
    report["image"]["sha256_after"] = sha256(image_path)
    report["image"]["unchanged"] = report["image"]["sha256_after"] == before
    report["finished_utc"] = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k not in ("warnings",)}, indent=2)[:6000])
    print(f"\nFull report: {out / 'report.json'}")
    ok = all(s["ok"] for s in report["steps"].values())
    return 0 if ok and not NETWORK_ATTEMPTS and report["image"]["unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
