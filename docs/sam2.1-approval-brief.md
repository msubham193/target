# SAM 2.1 — information for an approval decision

Status: **for the owner's decision. Nothing has been downloaded or installed** (K-03). Prepared 2026-10-21.

This brief covers what the owner asked for before approval: exact download sizes, dependencies, Windows / RTX 5060 compatibility, code and weight licences, training-data terms, and known restrictions. Sizes were read from HTTP `HEAD` responses and package-index metadata, so no file contents were fetched. Licences are as stated by the primary pages listed in §8. This is not legal advice: the owner, or counsel, should confirm before any use beyond local PoC evaluation (K-04).

## 1. What would be evaluated

**Person-guided outlining** (UC-R5). A person places points or a box on a raster's display image, and SAM 2.1 proposes an outline mask. The model has no categories, so it never labels what it outlines. Every outline would be a **proposed finding**: it is stored with its prompts, model, checkpoint hash and display settings, and needs a person's review before it becomes an observation (K-03). Only the image predictor would be used; no video, and no automatic "segment everything" mode.

## 2. Exact download sizes (weights)

Source: `https://dl.fbaipublicfiles.com/segment_anything_2/092824/`. Every file has Last-Modified 29 Sep 2024.

| Checkpoint | Parameters (published) | Bytes (Content-Length) | Size | ETag (for later integrity checks) |
|---|---|---|---|---|
| `sam2.1_hiera_tiny.pt` | 38.9 M | 156,008,466 | 156.0 MB (148.8 MiB) | `2a9d5f6d5f451bd71757590b89efa757-19` |
| `sam2.1_hiera_small.pt` | 46 M | 184,416,285 | 184.4 MB (175.9 MiB) | `705c3784f365637c2138c49ad33d98db-22` |
| `sam2.1_hiera_base_plus.pt` | 80.8 M | 323,606,802 | 323.6 MB (308.6 MiB) | `9a90111847fab62256c36acbfecdc2f2-39` |
| `sam2.1_hiera_large.pt` | 224.4 M | 898,083,611 | 898.1 MB (856.5 MiB) | `9f0bbfb809daef19db69d7f250218e1d-108` |
| **All four** | | 1,562,115,164 | 1.56 GB | |

- The ETags are multipart S3 ETags, not file hashes. After a download, a SHA-256 would be computed and recorded in `models/manifest.yaml`, as was done for the Phase 3 detector.
- **Recommendation:** approve **tiny and small first** (340.4 MB together). Add base_plus or large only if indicative runs show a need. D: has more than 360 GB free.

## 3. Code and Python dependencies

### Official code, not the PyPI package

- The package called **`sam2` on PyPI (1.1.0) was not published by Meta.** Its author is an individual, and its home page is a fork (`JinsuaFeito-dev/segment-anything-2`). **It should not be used.**
- Recommended: Meta's repository `facebookresearch/sam2` at a pinned commit, **`2b90b9f5ceec907a1c18123530e92e794ad901a4`** (16 Dec 2024, the newest commit on `main` as of 2026-10-21).
- Only the `sam2/` package directory (39 files, 375,194 bytes, including the four SAM 2.1 configs) is needed. The whole repository at that commit is 569 files and 64.6 MB, mostly notebooks and the web demo.

### Packages it would add to the API environment

From `setup.py` `install_requires`, with the versions that satisfy the pins:

| Package | Version | Download | Licence | Note |
|---|---|---|---|---|
| hydra-core | 1.3.7 | 168,790 B (wheel) | MIT | Configuration loader. `import sam2` initialises a process-wide Hydra instance, which is harmless with one job per process. |
| omegaconf | 2.3.x (hydra requires < 2.4) | 79,500 B (2.3.0 wheel) | BSD-3-Clause | |
| antlr4-python3-runtime | 4.9.3 (pinned `==4.9.*`) | 117,034 B (**source only**) | BSD-3-Clause | Pure Python, built locally. Needs no compiler. |
| iopath | 0.1.10 | 42,226 B (**source only**) | MIT | Pure Python, built locally. |
| portalocker | 4.4.1 | 129,702 B (wheel) | BSD-3-Clause (check at install) | iopath dependency |
| tqdm | 4.70.1 | 80,199 B (wheel) | MPL-2.0 AND MIT | Adds colorama on Windows if it is missing. |
| numpy, pillow, torch, torchvision, pyyaml | already installed | 0 | — | torch 2.11.0+cu128 and torchvision 0.26.0+cu128 meet `>=2.5.1` / `>=0.20.1`. |

The new Python packages total about 0.6 MB. Not needed, and not to be installed: the extras `notebooks`, `interactive-demo` and `dev`, and `huggingface_hub`, which `build_sam` uses only for its optional network path. The application would call `build_sam2(config, local_checkpoint)` with a local file only.

### Install method

The method matters on this machine:

- `pyproject.toml` lists **`torch` as a build requirement**. A default install would build in an isolated environment and download a **second, CPU-only PyTorch** (hundreds of MB). The install must use `--no-build-isolation` against the existing environment.
- `SAM2_BUILD_CUDA=0` must be set. Otherwise setup tries to compile a CUDA extension with `nvcc` and Visual C++, and neither is installed. That extension only does post-processing (removing small holes and specks from masks); the README says skipping it "shouldn't affect the results in most cases". **No compiled code would be added.** That avoids the Smart App Control and toolchain risk recorded in phase-5-readiness.md.

## 4. Windows and RTX 5060 compatibility

| Item | Status |
|---|---|
| GPU stack | **Verified** earlier (gpu-feasibility.md): torch 2.11.0+cu128 runs on the RTX 5060 (sm_120), driver 595.95, 8 GB VRAM with about 1.5 GB used by the desktop. |
| Official Windows support | **Not claimed by Meta.** INSTALL.md "strongly recommend[s]" WSL with Ubuntu on Windows. Native Windows is untested by the authors; it is an evaluation risk, not a known failure. |
| PyTorch version | SAM 2.1 was published against torch 2.5.1. Running on 2.11 is **expected** (pure PyTorch, standard operators) but **unverified**. It would be checked in step 1 of the evaluation. |
| Attention kernels | SAM 2 uses `scaled_dot_product_attention`. Which kernel PyTorch picks on Windows (flash, memory-efficient or maths) affects speed and memory, not results. **To measure.** |
| `torch.compile` / `vos_optimized` | Off by default and not to be used: it needs Triton, which has no official Windows build. Applies to video only. |
| Checkpoint loading | `torch.load(..., weights_only=True)` (in `build_sam.py`), which refuses arbitrary pickled code, a safety property for `.pt` files. |
| VRAM and speed | **Not measured.** The published FPS figures are for an A100 and do not transfer. Peak VRAM, latency per prompt and image-encoder time per 1024 px tile are step-1 measurements. |
| Offline operation | No network access is needed at run time with local checkpoints. It would be verified with the worker's network-isolation check, as in Phase 3. |

## 5. Licences

| Component | Licence | Source |
|---|---|---|
| SAM 2 code (model, training, demo) | **Apache-2.0** | Repository `LICENSE` and README |
| SAM 2.1 checkpoints | **Apache-2.0** (the README: "The SAM 2 model checkpoints … are licensed under Apache 2.0") | README |
| `cc_torch` connected components (only in the optional CUDA extension, **not built**) | BSD-3-Clause (`LICENSE_cctorch`) | Repository |
| Demo fonts (not used) | SIL OFL 1.1 | README |

Apache-2.0 permits commercial use, with notice and attribution conditions. **K-04 still applies:** this authorisation covers local PoC evaluation only, and no commercial-use permission is claimed here. A commercial decision would need its own review, including §6.

## 6. Training data and its terms

These apply to the data the model learned from, not to the weights' licence, but they are relevant to provenance and to any later fine-tuning.

| Dataset | Terms (as published) | Role |
|---|---|---|
| **SA-1B** (about 11 M images, 1.1 B masks; trained SAM 1 and SAM 2's image data) | **SA-1B Dataset Research License**: Meta's page lists intended use as research, and the licence as "Limited; see full license language for use". Faces and licence plates are blurred. | The **full licence text was not retrieved** (the download page needs an agreement). Verify before relying on it. **Not to be downloaded:** nothing in this plan needs it. |
| **SA-V** (50,583 training videos, 642,036 masklets) | **CC BY 4.0** for videos and annotations; terms of use must be accepted before download; evaluation code BSD-3-Clause | Video training data. Not needed. |
| Other data | The SAM 2 paper also describes internally licensed video data, not released. | — |

**Domain gap:** neither dataset is overhead or remote-sensing imagery. The SAM 2 paper does not evaluate on satellite or aerial imagery. Any statement about its quality on this project's rasters must come from our own evaluation (§7); no accuracy is claimed here (K-05).

## 7. Known restrictions and risks

1. **Class-agnostic:** SAM 2.1 outlines what a person points to. It cannot detect or label buildings, vehicles or land cover, and it would not be used to do so (K-03).
2. **Input is a display image:** it expects 8-bit RGB. Multispectral or 16-bit rasters are fed through the Phase 3b display settings (bands and stretch). Those settings are recorded with every outline, because a different stretch can give a different mask.
3. **Image resolution:** the encoder works at 1024 × 1024. Large rasters need tiling or a cropped window around the prompt; the crop and scale are recorded. Outlines are converted to map coordinates only through valid georeferencing. Otherwise they stay in pixel space, as measurements do.
4. **Unsupported on Windows by the authors** (see §4). There is a fallback if native Windows fails: WSL2 is possible, but it is a larger change and would come back for separate approval.
5. **Mask post-processing is off** (no CUDA extension). Small holes and specks may appear; the reviewer edits or rejects.
6. **OPS-001:** outlines are descriptive geometry for review. No targeting, engagement or impact-point use.

## 8. Evaluation outline after approval

These are indicative runs on synthetic and approved imagery, with no thresholds set beforehand (K-05):

1. Install as in §3 into the existing environment; record the package versions and checkpoint SHA-256 values in the model manifest.
2. Offline smoke test: the worker capability probe, the network-isolation check, and peak VRAM and latency for tiny and small on 1024 px windows.
3. A repeatability check: the same prompts on the same image give the same mask (bitwise or IoU = 1.0, to be reported, not assumed).
4. Indicative quality: IoU against reviewed reference outlines on the representative imagery set (imagery-inventory-and-benchmark-plan.md), reported per image category without a pass mark.
5. Then report back for the decision to integrate.

## 9. Decision requested

| # | Item | Size | Recommendation |
|---|---|---|---|
| 1 | Clone or archive `facebookresearch/sam2` at commit `2b90b9f`; install it with `SAM2_BUILD_CUDA=0 --no-build-isolation` | 375 KB of package code, plus 0.6 MB of Python dependencies | Approve, for local evaluation |
| 2 | `sam2.1_hiera_tiny.pt` | 156.0 MB | Approve |
| 3 | `sam2.1_hiera_small.pt` | 184.4 MB | Approve |
| 4 | `sam2.1_hiera_base_plus.pt` / `sam2.1_hiera_large.pt` | 323.6 MB / 898.1 MB | Defer until indicative runs |
| 5 | SA-1B, SA-V or any other dataset | — | Not requested |

## Sources

- Repository, README, licences and checkpoint list: <https://github.com/facebookresearch/sam2>
- Install notes (Windows, CUDA extension): <https://github.com/facebookresearch/sam2/blob/main/INSTALL.md>
- Dependencies: <https://github.com/facebookresearch/sam2/blob/main/setup.py>, `pyproject.toml`
- Release notes (SAM 2.1, 30 Sep 2024): <https://github.com/facebookresearch/sam2/blob/main/RELEASE_NOTES.md>
- Paper: <https://arxiv.org/abs/2408.00714>
- SA-V: <https://ai.meta.com/datasets/segment-anything-video/>
- SA-1B: <https://ai.meta.com/datasets/segment-anything/>
- Package metadata: <https://pypi.org/project/sam2/> (third-party), <https://pypi.org/project/hydra-core/>, <https://pypi.org/project/iopath/>, <https://pypi.org/project/omegaconf/>
