# SAM 2.1 compatibility and licensing gate

Status, 2026-10-21: **steps 1–3 complete; stopped at step 4, awaiting explicit download approval.** Nothing has been downloaded: no code, no weights, no packages, no datasets. No environment was created or changed. Step 5, the compatibility test, has **not been run**, so this report contains **no inference results**.

The experiment files are written and can be reviewed before approval. They are in `experiments/sam2_compat/`, are not imported by the application, and have not been run:

- `requirements-sam2-eval.txt`
- `setup_env.ps1`, which refuses to run without `-Approved`
- `compat_test.py`
- `rollback.ps1`

## 1. Environment (recorded 2026-10-21, read-only)

| Item | Value |
|---|---|
| Windows | Windows 11 Home Single Language 10.0.26200 (build 26200), 64-bit |
| CPU / RAM | Intel Core i5-14400F / 15.7 GB |
| Smart App Control | Enforcement on (`VerifiedAndReputablePolicyState = 1`) |
| GPU | NVIDIA GeForce RTX 5060, compute capability 12.0 (Blackwell, sm_120), 8,151 MiB |
| VRAM in use at idle | 1,630 MiB used / 6,266 MiB free (nvidia-smi); PyTorch reports 7,031 MiB free in its own process |
| NVIDIA driver | 595.95 (supports CUDA up to 13.2) |
| CUDA toolkit / `nvcc` | Not installed, not on PATH (none needed; §4) |
| Application Python | CPython 3.11.9, venv `D:\geopoc-cache\venv-api` (4.43 GB, 69 packages; freeze snapshot SHA-256 `4A5690F8…37BB52F`), base interpreter `C:\Users\HP\AppData\Local\Programs\Python\Python311` |
| PyTorch | torch 2.11.0+cu128, torchvision 0.26.0+cu128; CUDA runtime 12.8; cuDNN 9.19; CUDA available; architectures sm_75…sm_120 |
| Attention kernels (bf16, this GPU, Windows) | **Flash attention: not available** ("No available kernel"); memory-efficient: works; maths: works; bf16 supported |
| uv | 0.10.7; cache `D:\geopoc-cache\uv` |
| WSL | Installed, but only Docker Desktop's internal distribution; no Ubuntu distribution |
| Disk | **C: 4.1 GB free** (too little; nothing goes there); **D: 373.3 GB free** |
| Repository | Branch `main` at `f51021f`; 56 modified and 118 untracked files (Phases 3–3b and the sidecar follow-up; uncommitted, as instructed) |

## 2. Licensing gate

Primary sources were read on 2026-10-21. The code, the checkpoints and the training data are licensed separately and are kept apart below.

### 2.1 Code: `facebookresearch/sam2` at commit `2b90b9f5ceec907a1c18123530e92e794ad901a4`

- **Commit:** 16 Dec 2024, the newest commit on `main` on 2026-10-21. The repository is not archived.
- **`LICENSE` at that commit** (11,357 bytes, SHA-256 `c71d239d…`): **Apache License 2.0**. Compared with the canonical text from apache.org, the only difference is one leading blank line; there are no added clauses.
- **README at that commit**, "License": *"The SAM 2 model checkpoints, SAM 2 demo code (front-end and back-end), and SAM 2 training code are licensed under Apache 2.0"*. The demo's Inter and Noto Color Emoji fonts are under the SIL OFL 1.1; they are not fetched (§3).
- **`LICENSE_cctorch`** (BSD-3-Clause) covers an optional GPU connected-components step. It is part of the CUDA extension, which is **not built**.
- **Not used:** the PyPI package `sam2` was published by an individual, not by Meta.

### 2.2 Checkpoints: `sam2.1_hiera_tiny.pt` and `sam2.1_hiera_small.pt`

- **Terms:** Apache 2.0, by the README sentence quoted above. Meta's own Hugging Face model cards (`facebook/sam2.1-hiera-tiny`, `facebook/sam2.1-hiera-small`) also state `license: apache-2.0`, and the files are not gated.
- **Distribution:** `https://dl.fbaipublicfiles.com/segment_anything_2/092824/`, the URLs in `checkpoints/download_ckpts.sh` at the pinned commit. The server is Amazon S3 (CloudFront) and presents no click-through terms.
- **Integrity:** the sizes from Meta's server match the sizes in Hugging Face's listings of the same files exactly. Hugging Face publishes their SHA-256, which `setup_env.ps1` checks after download:

  | File | Bytes | Expected SHA-256 |
  |---|---|---|
  | `sam2.1_hiera_tiny.pt` | 156,008,466 | `7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69` |
  | `sam2.1_hiera_small.pt` | 184,416,285 | `6d1aa6f30de5c92224f8172114de081d104bbd23dd9dc5c58996f0cad5dc4d38` |

- **Loading safety:** SAM 2 loads checkpoints with `torch.load(..., weights_only=True)`, which refuses to run arbitrary pickled code.

### 2.3 Training data: terms, not downloaded, not needed

- **What the model was trained on.** The SAM 2 paper (arXiv 2408.00714 v2, Appendix D.2) says *"Our released model is trained on SA-V manual + Internal and SA-1B"*, in a mixture of about 15.2 % SA-1B, 70 % SA-V and 14.8 % internal data. It also says *"We also used internally available licensed video data"*. The paper's results are for SAM 2.1.
- **SA-1B.** The original Segment Anything paper's datasheet (arXiv 2304.02643) says:
  - *"to be used for research purposes only"*;
  - *"released under a license agreement allowing use for certain research purposes"*;
  - *"Users must agree to the terms of use before downloading or using the dataset"*;
  - the images were *"licensed from a third party photo company"*, and faces and licence plates are blurred.

  The licence is named the **SA-1B Dataset Research License**. Its **full text was not retrievable**: Meta's download pages refuse automated requests (HTTP 400/404) and sit behind a click-through agreement.
- **SA-V.** **CC BY 4.0** for the videos and annotations (`sav_dataset/README.md` at the pinned commit). The evaluation code is BSD-3-Clause.
- **Internal licensed video data:** not released; no terms are published.
- **No other datasets** are proposed. SA-1B and SA-V are not downloaded.

### 2.4 Dependencies added by the chosen method (§3)

Only the evaluation environment gets these. Versions, file hashes and declared licences are from PyPI metadata.

| Package | Version | File (bytes) | Licence | Needed by |
|---|---|---|---|---|
| hydra-core | 1.3.2 (2023-02-23) | wheel, 154,547 | MIT | `sam2.build_sam` (sam2 requires ≥1.3.2) |
| omegaconf | 2.3.0 (2022-12-08) | wheel, 79,500 | BSD-3-Clause | hydra-core (<2.4) |
| antlr4-python3-runtime | 4.9.3 (2021-11-06) | source, 117,034 | BSD-3-Clause | hydra-core / omegaconf (==4.9.*) |
| iopath | 0.1.10 (2022-07-09) | source, 42,226 | MIT | sam2 `setup.py` |
| portalocker | 4.3.2 (2026-09-14) | wheel, 128,984 | BSD-3-Clause | iopath. 4.x has no `pywin32` dependency on Windows, which would be a compiled package under Smart App Control. |
| tqdm | 4.67.1 (2024-11-24) | wheel, 78,540 | MPL-2.0 AND MIT | sam2, iopath |

These are reused read-only from the application environment, not reinstalled: torch, torchvision, numpy, pillow, PyYAML, packaging, typing-extensions, colorama and setuptools. MPL-2.0 (tqdm) is file-level copyleft. It applies to tqdm's own files only if they are modified and distributed; neither happens here.

### 2.5 Conclusion of the licensing gate

**No material licence question blocks a local PoC evaluation.** The code and the checkpoints are Apache-2.0 by Meta's own statements, and the added packages are permissive. Two questions remain unresolved; they matter only beyond local evaluation, and need the owner's or counsel's decision before any such use (K-04):

1. **SA-1B terms and the weights.** The SA-1B licence restricts *the dataset* to research use, and its full text could not be read. Meta licenses the weights trained on it under Apache 2.0. Whether the dataset's terms affect any use of the weights is a legal question that is not answered here. It does not affect local evaluation, which is consistent with research use in any case.
2. **The internal licensed video data** has no published terms.

No commercial-use permission is claimed.

## 3. Isolated compatibility plan

### 3.1 Exact download list

| # | Item | Source | Size |
|---|---|---|---|
| 1 | SAM 2 code at `2b90b9f5…ad901a4`: only `sam2/` (39 files) plus `LICENSE`, `LICENSE_cctorch`, `README.md`, `setup.py` and `pyproject.toml`. Fetched as a sparse, blobless git fetch of that one commit. | `https://github.com/facebookresearch/sam2.git` | 409,537 bytes of files, plus git commit and tree objects (estimated under 0.2 MB) |
| 2 | `sam2.1_hiera_tiny.pt` | `https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt` | 156,008,466 bytes |
| 3 | `sam2.1_hiera_small.pt` | `https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt` | 184,416,285 bytes |
| 4 | The six packages in §2.4, by exact SHA-256 | `https://pypi.org` (files.pythonhosted.org) | 600,831 bytes |
| | **Total download** | | **about 341.4 MB** (341,435,119 bytes plus git metadata) |
| | **Total storage on D:** | checkpoints 340.4 MB, code 0.4 MB, evaluation environment and built packages about 5 MB, uv cache about 2 MB, results under 10 MB | **under 360 MB** (D: has 373.3 GB free) |

Not downloaded:

- the base_plus and large checkpoints;
- the rest of the repository at that commit: 64.2 MB of notebooks, web demo, SA-V tools and images;
- any dataset;
- a second PyTorch, CUDA toolkit, Visual Studio or WSL distribution;
- `huggingface_hub`.

### 3.2 Exact commands

These are in `experiments/sam2_compat/setup_env.ps1`, which runs only with `-Approved`:

```powershell
# code: one commit, sparse, blobless; verified by commit hash and git fsck
git init -q D:\geopoc-cache\sam2-eval\src
git -C D:\geopoc-cache\sam2-eval\src remote add origin https://github.com/facebookresearch/sam2.git
git -C D:\geopoc-cache\sam2-eval\src sparse-checkout set --no-cone /sam2/ /LICENSE /LICENSE_cctorch /README.md /setup.py /pyproject.toml
git -C D:\geopoc-cache\sam2-eval\src fetch -q --depth 1 --filter=blob:none origin 2b90b9f5ceec907a1c18123530e92e794ad901a4
git -C D:\geopoc-cache\sam2-eval\src checkout -q 2b90b9f5ceec907a1c18123530e92e794ad901a4

# checkpoints: size and SHA-256 checked; a mismatch deletes the file and stops; then made read-only
curl.exe -fL --retry 3 -o D:\geopoc-cache\sam2-eval\checkpoints\sam2.1_hiera_tiny.pt.part https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt
curl.exe -fL --retry 3 -o D:\geopoc-cache\sam2-eval\checkpoints\sam2.1_hiera_small.pt.part https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt

# separate environment from the same base interpreter; app packages visible read-only through a .pth file
uv venv -q --python C:\Users\HP\AppData\Local\Programs\Python\Python311\python.exe D:\geopoc-cache\venv-sam2-eval
#   D:\geopoc-cache\venv-sam2-eval\Lib\site-packages\_sam2_eval_paths.pth contains two lines:
#   D:\geopoc-cache\sam2-eval\src
#   D:\geopoc-cache\venv-api\Lib\site-packages
uv pip install --python D:\geopoc-cache\venv-sam2-eval\Scripts\python.exe --no-deps --require-hashes --no-build-isolation -r experiments\sam2_compat\requirements-sam2-eval.txt

# then, with a prompt placed by a person on the synthetic image (see §4):
D:\geopoc-cache\venv-sam2-eval\Scripts\python.exe experiments\sam2_compat\compat_test.py --checkpoint tiny --point 380,300
D:\geopoc-cache\venv-sam2-eval\Scripts\python.exe experiments\sam2_compat\compat_test.py --checkpoint small --box 250,170,510,430
```

### 3.3 How the plan avoids a second PyTorch

- The evaluation environment installs **no torch or torchvision**.
- The application's site-packages directory is added to the evaluation environment's `sys.path` through a `.pth` file. The existing torch 2.11.0+cu128 is therefore *imported* from where it is, read-only.
- Packages are installed with `--no-deps`, so nothing can pull in or upgrade torch. With `--require-hashes`, only the six exact files are accepted.
- SAM 2 itself is **not pip-installed**: its source directory is on the path, as its INSTALL.md describes. This matters because `sam2`'s `pyproject.toml` declares `torch>=2.5.1` as a *build* requirement, and a normal install would download a second, CPU-only PyTorch into an isolated build environment.
- The two source-only packages (antlr4 and iopath, both pure Python) are built with `--no-build-isolation`, using the setuptools already present. No build tools are downloaded.

### 3.4 How the plan avoids compiling CUDA extensions

- `setup.py` is never run, because SAM 2 is not installed. Its optional CUDA extension (`sam2/csrc/connected_components.cu`) therefore cannot be compiled.
- `setup_env.ps1` stops if any compiled `sam2/_C*` file is present.
- Without the extension, SAM 2 skips its small-hole and speck post-processing and warns. The README says this "shouldn't affect the results in most cases"; the test records the warning.
- No compiler, CUDA toolkit or Visual Studio is needed or installed. Nothing compiled is added, so Smart App Control is not engaged.

### 3.5 Isolation from the application

| Concern | Measure |
|---|---|
| Python environment | A separate venv, `D:\geopoc-cache\venv-sam2-eval`. The app venv is only read. Its package list is frozen before setup and compared after; any difference stops the setup. |
| Files | Everything under `D:\geopoc-cache\sam2-eval` (code, checkpoints, results). Not the application's data root (`D:\geopoc-data`), its weights folder, or the repository's `models/manifest.yaml`. |
| Database and API | Not used. `compat_test.py` imports nothing from `geopoc`, writes no database rows and starts no service. It refuses to run inside the app venv. |
| Network | An in-process guard blocks `socket.connect`, `connect_ex`, `create_connection` and `getaddrinfo` before anything is imported, and records any attempt. `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE` are set. A run counts as offline only if no attempt is recorded. |
| Inputs | A generated synthetic image, or a given file. Its SHA-256 is compared before and after. |
| GPU | Runs as its own process. VRAM is released when it exits, as for the application's job processes. It should not be run while the application has a GPU job active. |

### 3.6 Rollback

`experiments/sam2_compat/rollback.ps1`:

- deletes `D:\geopoc-cache\venv-sam2-eval` and `D:\geopoc-cache\sam2-eval`;
- clears the six packages from the uv cache;
- prints the app environment's package list, to confirm torch 2.11.0+cu128 and the 69 packages are unchanged.

The application environment, database, data and configuration are never changed, so there is nothing to restore. The `experiments/sam2_compat/` files are untracked and can be deleted.

## 4. Approval requested

Please approve or decline each item:

| # | Item | Size |
|---|---|---|
| A1 | SAM 2 code, sparse fetch of commit `2b90b9f5ceec907a1c18123530e92e794ad901a4` (§3.1, item 1) | about 0.4–0.6 MB |
| A2 | `sam2.1_hiera_tiny.pt`, SHA-256 `7402e0d8…34be69` | 156.0 MB |
| A3 | `sam2.1_hiera_small.pt`, SHA-256 `6d1aa6f3…dc4d38` | 184.4 MB |
| A4 | hydra-core 1.3.2, omegaconf 2.3.0, antlr4-python3-runtime 4.9.3, iopath 0.1.10, portalocker 4.3.2, tqdm 4.67.1, by the hashes in `requirements-sam2-eval.txt` | 0.6 MB |
| A5 | Creating `D:\geopoc-cache\venv-sam2-eval` and `D:\geopoc-cache\sam2-eval` as in §3 | under 360 MB in total |
| A6 | The prompt for the test. Default: a positive point at (380, 300) and a box (250, 170)–(510, 430), placed on the generated synthetic image's disk. Alternatively, a point or box you give. | — |

## 5. Compatibility test protocol (after approval; not run)

`compat_test.py` runs tiny, then small, on the GPU with the prompt from A6, and records:

- loading and inference: success, or the exact exception and traceback;
- peak GPU memory (PyTorch allocated and reserved) and free VRAM before;
- the time for the first `set_image` and `predict`, then five repeats (warm), in float32;
- bfloat16 autocast separately, as in Meta's example;
- repeatability across repeats: identical masks, IoU of the best mask, and the largest logit difference;
- invalid prompts and empty outputs: what the model does in each case, so the application can validate prompts before calling it. The cases are a point outside the image, a negative point only, a NaN coordinate, a label other than 0/1, an inverted box, a zero-area box, no prompt, a point on uniform background, and an all-black image;
- offline operation (network attempts recorded) and that the input file is unchanged;
- every warning raised, for example the skipped post-processing and the attention-kernel fallback.

Results would go only to `D:\geopoc-cache\sam2-eval\results\…\report.json`, plus the best mask as a PNG. **No accuracy is claimed from synthetic inputs.**

If native Windows fails, the exact error will be reported. An alternative such as a WSL Ubuntu distribution would need a separate approval: it means a distribution download of several GB, a second PyTorch inside WSL, and an environment change.

## 6. Known risks, to be settled by the test

1. **Windows is not supported by Meta** (INSTALL.md recommends WSL). Nothing is known to fail; it is untested.
2. **PyTorch 2.11 versus the 2.5.1 SAM 2.1 was released with.** The code is plain PyTorch. `torch.backends.cuda.sdp_kernel`, which code of that period used, still exists in 2.11.
3. **Flash attention is not available** on this Windows build for sm_120; memory-efficient attention works. Whether SAM 2 falls back cleanly or fails when it asks for flash attention is not yet known. It affects speed and memory, not results.
4. **VRAM:** about 6.3–7.0 GB free. Tiny and small are expected to fit (38.9 M and 46 M parameters), but this is not measured.
5. **Hydra global state:** `import sam2` initialises a process-wide Hydra instance. It is harmless in a separate process, which is how the application runs jobs; this matters only for later integration.
6. **No post-processing** without the CUDA extension (§3.4).

## 7. Results

**None yet.** The test has not been run, because the downloads are not approved.

## 8. Go / no-go

- **The isolated compatibility experiment: GO, subject to approval** of A1–A6. The licensing gate shows no material blocker for local evaluation, and the plan changes nothing in the application.
- **Application integration: NO-GO at this gate.** There is no evidence yet that SAM 2.1 loads and runs on this machine. Integration should be reconsidered only after:
  - the compatibility test passes (loading, inference, offline, repeatability, memory within budget);
  - the owner reviews the results;
  - the evaluation plan's indicative runs on approved imagery are done.

  Integration would also have to treat every outline as a proposed finding for review (K-03), and would need its own approval.

## Sources

- Repository and the files at the commit: <https://github.com/facebookresearch/sam2/tree/2b90b9f5ceec907a1c18123530e92e794ad901a4>. `LICENSE`, `LICENSE_cctorch`, `README.md`, `checkpoints/download_ckpts.sh`, `sav_dataset/README.md` and `INSTALL.md` were read from `raw.githubusercontent.com` at that commit.
- Canonical Apache 2.0 text: <https://www.apache.org/licenses/LICENSE-2.0.txt>
- Checkpoint server headers: `https://dl.fbaipublicfiles.com/segment_anything_2/092824/` (HTTP HEAD only)
- Hugging Face model metadata and file hashes: <https://huggingface.co/facebook/sam2.1-hiera-tiny>, <https://huggingface.co/facebook/sam2.1-hiera-small> (API metadata only)
- SAM 2 paper: <https://arxiv.org/abs/2408.00714> (HTML v2, Appendix D.2)
- Segment Anything paper and SA-1B datasheet: <https://arxiv.org/abs/2304.02643> (HTML v1); README: <https://github.com/facebookresearch/segment-anything>
- Package metadata: <https://pypi.org/project/hydra-core/1.3.2/>, <https://pypi.org/project/omegaconf/2.3.0/>, <https://pypi.org/project/antlr4-python3-runtime/4.9.3/>, <https://pypi.org/project/iopath/0.1.10/>, <https://pypi.org/project/portalocker/4.3.2/>, <https://pypi.org/project/tqdm/4.67.1/>; iopath `setup.py` at tag v0.1.10
