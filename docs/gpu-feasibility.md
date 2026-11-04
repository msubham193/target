# GPU and Model Feasibility

Date: 2026-10-12. Everything here was observed on the development machine. Automated tests and manual checks are labelled separately.

## Findings

1. **CUDA-enabled PyTorch works on the RTX 5060.** PyTorch 2.11.0+cu128 with torchvision 0.26.0+cu128 runs real operations on the GPU.
2. **One ground-image model is integrated and runs on the GPU**: `fasterrcnn_resnet50_fpn_v2` (COCO). See its [model card](../models/cards/fasterrcnn_resnet50_fpn_v2.md).
3. **Its accuracy is unknown.** No representative or labelled data exists, so nothing was evaluated.
4. **GPU memory is released after every job.** Whether a vision model and a Qwen model fit together was not tested and is not assumed.

## Hardware and environment

| | |
|---|---|
| GPU | NVIDIA GeForce RTX 5060, 8,151 MiB, compute capability 12.0 |
| Driver | 595.95 (supports CUDA up to 13.2) |
| VRAM already in use by the desktop | about 1.5 GB |
| Python | 3.11.9 |
| Disk before installing | C: 6.7 GB free, D: 373 GB free |

## Choosing the PyTorch build

The official PyTorch wheel index was queried (dry run, nothing installed) for Python 3.11 on Windows:

| Index | Resolved to | Assessment |
|---|---|---|
| `cu128` (CUDA 12.8) | torch 2.11.0, torchvision 0.26.0 | **Chosen** |
| `cu129` | torch 2.9.0 with a mismatched torchvision | Not a usable pair |
| `cu130` (CUDA 13.0) | torch 2.14.1, torchvision 0.29.1 | Newest; not tested |

Reasons for `cu128`:

- An RTX 50-series card needs a build compiled for its architecture (`sm_120`). The `cu128` build lists it.
- Windows Smart App Control on this machine had blocked the newest release of every native package tried so far (psycopg, PyProj, Rasterio). The slightly older build was the safer first attempt, and it loaded.
- The driver supports it.

`cu130` may also work. It was not tried, because a working build was found and a second multi-gigabyte download was not justified.

### Verified before touching the project

In a throwaway environment on D: (manual check):

- `torch.cuda.is_available()` → true; device "NVIDIA GeForce RTX 5060"; capability (12, 0)
- Architectures compiled in: `sm_75, sm_80, sm_86, sm_90, sm_100, sm_120`
- A tensor operation on the GPU returned the correct value
- A convolution ran through cuDNN 9.19.0
- torchvision's non-maximum suppression ran on the GPU
- Smart App Control did not block any of it

### Installation

- Added as an **optional** dependency group: `uv sync --group ml`. Without it the application runs and analysis models are reported as unavailable.
- No dependency conflict: PyTorch, Rasterio, PyProj, psycopg, Pillow and NumPy 2.4.6 import together.
- The Python environment was moved to `D:\geopoc-cache\venv-api` (set by `scripts\env.ps1`). It is 4.4 GB, which would not fit on C:. Source code stays in the project folder.
- The download cache on D: grew to about 9.5 GB.
- No system-level change was made: no CUDA toolkit, no driver change, no Smart App Control change.

## The model

| | |
|---|---|
| Model | `fasterrcnn_resnet50_fpn_v2`, weights `COCO_V1`, from torchvision |
| Size checked before download | 167.1 MB (D: had 364 GB free) |
| Installed to | `D:\geopoc-data\weights\…`, outside the repository |
| Checksum | SHA-256 `dd69338a…706bf`, matching the hash fragment in the published file name; recorded in the manifest and verified before every load |
| Configuration | Entirely in `models/manifest.yaml`. Pipeline code has no model settings. |
| Loading | From the local file. Verified with network connections blocked. |

The weights are downloaded once by an explicit command (`python -m geopoc.devtools fetch-model …`). The application never downloads anything.

## Test results

### Automated (run by `scripts\check.ps1`; skipped automatically where the GPU or weights are absent)

| Test | Result |
|---|---|
| A real tensor operation runs on the GPU, and the build includes this GPU's architecture | Pass |
| Installed weights match the manifest checksum and size | Pass |
| The model loads from local weights with network connections blocked | Pass |
| Inference on the GPU returns well-formed output; peak GPU memory under 6 GB | Pass |
| The real model runs through a job; the run records the GPU, weights checksum and library versions; every observation satisfies the contract and has no geometry | Pass |

These prove the inference path works. They use a synthetic image with no real objects, so they say nothing about accuracy.

### Manual (one run, 2026-10-12)

A standard public-domain 512 × 512 test photograph of one person was uploaded through a real API process and analysed by a real worker process.

| Measurement | Value |
|---|---|
| Worker's self-report | torch 2.11.0+cu128, CUDA available, RTX 5060 |
| Whole job | 5.4 s |
| Model load / inference | 0.62 s / 0.74 s (single first call, includes warm-up) |
| PyTorch peak GPU allocation | 470 MB |
| System-wide GPU memory: before / peak / after | 1,503 / 2,328 / 1,499 MB |
| Detections | 300 raw; 2 at or above the 0.5 cutoff; 298 below |
| Stored findings | `coco:person` 0.994, `coco:person` 0.681; both `needs_review`; neither has a geometry |

The first box covers most of the frame, which is consistent with the one person this standard image is known to show. Neither box was inspected visually, and the second, a narrow strip at the image edge, may well be a false positive. One image is neither a benchmark nor an evaluation.

## Memory and the Qwen question

- This model used about 0.8 GB of VRAM at peak, system-wide.
- VRAM returned to its starting level when the job's process exited. That is by design: each job runs in its own process.
- **Not tested:** running any Qwen model, alone or alongside a vision model. The Qwen builds present in Ollama are about 6 GB and 23 GB; with roughly 6.5 GB of VRAM free, co-loading cannot be assumed.
- The design therefore never needs both loaded at once: a vision job ends and frees its memory before anything else starts. Ollama's own unloading behaviour still has to be measured in Phase 5.

## Not done, and not claimed

- No accuracy, precision or recall figure for any model.
- No calibration and no validated threshold.
- No throughput or batch measurement, no large-image timing, no CPU-only timing.
- No overhead-raster or video model.
- No Qwen inference.
- No test of the `cu130` build.

## Reproducing

```powershell
. .\scripts\env.ps1
Set-Location services\api
uv sync --group ml
uv run python -m geopoc.jobs.probe
uv run python -m geopoc.devtools fetch-model fasterrcnn_resnet50_fpn_v2@coco_v1
uv run pytest tests/test_gpu_model.py -rs
```
