# Environment Assessment — Geospatial PoC (Phase 0)

Assessed: 2026-10-12, on the development machine, working directory `C:\Users\HP\Desktop\target`.

Everything under "Observed" was produced by a command that actually ran in this session. Anything not observed is listed under "Not verified". No packages were installed, no models downloaded, no settings changed.

## 1. Commands used

All commands were read-only. Environment variables were listed by **name and value length only**; no values were printed.

| # | Purpose | Command (PowerShell 5.1) |
|---|---------|--------------------------|
| 1 | Repository contents | `Get-ChildItem -Force`, `Get-ChildItem docs -Recurse -Force` |
| 2 | OS, RAM | `Get-CimInstance Win32_OperatingSystem`, `$PSVersionTable` |
| 3 | Tool discovery | `Get-Command` for git, python, py, node, npm, pnpm, yarn, docker, psql, nvidia-smi, ffmpeg, ffprobe, gdalinfo, conda, uv, ollama, wsl |
| 4 | Tool versions | `git --version`, `python --version`, `py -0p`, `node --version`, `npm --version`, `pnpm --version`, `uv --version`, `ffmpeg -version`, `ollama --version`, `docker --version`, `docker compose version` |
| 5 | GPU | `nvidia-smi`, `nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,memory.free,compute_cap --format=csv`, `Get-CimInstance Win32_VideoController` |
| 6 | CPU, disks | `Get-CimInstance Win32_Processor`, `Get-CimInstance Win32_LogicalDisk` |
| 7 | Docker / WSL | `docker info --format ...`, `docker images`, `docker ps -a`, `wsl --status`, `wsl -l -v` |
| 8 | Local LLM runtime | `ollama list`, `ollama ps`, `ollama show <model>` |
| 9 | Python packages | `python -m pip list` (filtered), `importlib.util.find_spec` for geospatial/DB/test modules |
| 10 | PyTorch / ONNX GPU | `python -c "import torch; torch.cuda.is_available() ..."`, `onnxruntime.get_available_providers()` |
| 11 | Env var names | `Get-ChildItem Env:` filtered by name pattern, printing name + length only |
| 12 | PostgreSQL | `Get-Service *postgres*`, `Test-Path 'C:\Program Files\PostgreSQL'`, `docker exec apexiq-postgres psql ... pg_available_extensions` |
| 13 | Misc | `Get-ItemProperty ... LongPathsEnabled`, `Get-ExecutionPolicy -List`, `Get-NetTCPConnection -State Listen`, `ffmpeg -hwaccels` |
| 14 | Spec integrity | `Get-FileHash ... -Algorithm SHA256` |

## 2. Results actually observed

### 2.1 System

| Item | Observed |
|------|----------|
| OS | Windows 11 Home Single Language, 10.0.26200, 64-bit |
| Shell | Windows PowerShell 5.1.26100.9444 (Git Bash also available) |
| CPU | Intel Core i5-14400F, 10 cores / 16 logical processors |
| RAM | 16 GB total (15.7 GiB visible); **about 1.65 GB free at the time of the check** |
| Disk C: | 464.2 GB, **8.4 GB free** |
| Disk D: | 488.3 GB, 374 GB free |
| Long paths | `LongPathsEnabled = 0` (disabled) |
| Execution policy | CurrentUser = RemoteSigned (sufficient for `npm.ps1` / `pnpm.ps1`) |
| Repository | Not a git repository. Contains only `docs/Geospatial_PoC_UI_and_AI_Analysis_Rules_v3.docx` (55,956 bytes, SHA-256 `61A16836F21BC868D97545FC94976AEF64EB92C71B8A8D84922D3262FF812D58`) |

### 2.2 Toolchain

| Tool | Observed |
|------|----------|
| Git | 2.53.0.windows.1 |
| Python | 3.11.9 (`C:\Users\HP\AppData\Local\Programs\Python\Python311`); a uv-managed CPython 3.14.3 is also registered |
| uv | 0.10.7 |
| Node.js | v24.14.0 |
| npm / pnpm | 11.9.0 / 10.33.0 (yarn not found) |
| Docker | Engine 29.2.1, Compose v5.0.2, Docker Desktop on WSL 2 (only distro: `docker-desktop`) |
| Docker VM | 16 CPUs, about 7.6 GiB memory; an `nvidia` container runtime is listed |
| FFmpeg | 9.0.1 full build (gyan.dev, installed via WinGet); `ffprobe` present; `cuda`, `d3d11va`, `d3d12va`, `qsv`, `vulkan` hwaccels listed |
| Ollama | 0.40.2, listening on `127.0.0.1:11434`; no model loaded at check time |
| conda | Not found |
| GDAL CLI (`gdalinfo`) | Not found; no OSGeo4W or QGIS install detected |
| PostgreSQL (native) | Not installed: no `psql`, no service, no `C:\Program Files\PostgreSQL` |

### 2.3 GPU

| Item | Observed |
|------|----------|
| Model | NVIDIA GeForce RTX 5060 |
| VRAM | 8151 MiB total; 1148 MiB already used by desktop applications; 6748 MiB free |
| Driver | 595.95 (WDDM); `nvidia-smi` reports "CUDA Version: 13.2" (the maximum CUDA API the driver supports, not an installed toolkit) |
| Compute capability | 12.0 |
| CUDA toolkit | No `CUDA_PATH`/`CUDA_HOME` environment variable is set; no standalone toolkit was detected |
| PyTorch | `torch 2.10.0+cpu`, `torchvision 0.25.0+cpu` — **CPU-only build; `torch.cuda.is_available()` is False** |
| ONNX Runtime | 1.24.4 with `CPUExecutionProvider` and `AzureExecutionProvider` only — no CUDA provider |

### 2.4 Python packages in the global 3.11 interpreter

Present: `fastapi 0.129.0`, `uvicorn 0.41.0`, `pydantic 2.12.5`, `numpy 1.26.4`, `pillow 10.4.0`, `shapely 2.1.2`, `scikit-image 0.26.0`, `torch 2.10.0+cpu`, `torchvision 0.25.0+cpu`, `transformers 4.41.2`, `accelerate 0.33.0`, `onnxruntime 1.24.4`, `ollama 0.6.1`, `cv2` (OpenCV), `av` (PyAV).

Missing: `rasterio`, `pyproj`, `osgeo` (GDAL bindings), `fiona`, `geopandas`, `sqlalchemy`, `alembic`, `psycopg`/`psycopg2`, `pytest`, `decord`.

These are global packages belonging to other work on this machine. The project should not depend on them (see section 5).

### 2.5 Local models registered in Ollama

| Tag | Size | Details from `ollama show` | Relevance |
|-----|------|----------------------------|-----------|
| `qwen3.6:35b-a3b` (= `qwen3.6:latest`) | 23 GB | 36.0B parameters, MoE architecture, Q4_K_M, 262,144 context, capabilities: completion, vision, tools, thinking | Qwen family, but far larger than the 8 GB of VRAM (see risks) |
| `qwen2.5vl:latest` | 6.0 GB | 8.3B parameters, Q4_K_M, 128,000 context, capabilities: completion, vision | Qwen family; closer to the VRAM budget |
| `edvaqwen:latest` | 4.7 GB | 7.6B, 4096 context, custom system prompt for an unrelated education product | Not suitable: built for another project |
| `llama3.1:8b`, `llama3.2:latest`, `glm-4.7-flash:latest` | 4.9 / 2.0 / 19 GB | Not inspected | Not Qwen; out of scope for the "configurable Qwen" requirement |
| `glm-5:cloud`, `kimi-k2.5:cloud` | no local size | Cloud-routed tags | **Must never be used: inference would leave the machine** |

### 2.6 Docker state

- Images present: `postgres:17`, `postgres:16`, `postgres:16-alpine`, `redis:7`, `redis:7-alpine`. **No PostGIS image is present.**
- Running containers belonging to other projects: `apexiq-postgres` (publishes host port 5432), `apexiq-redis` (6379). `eddva-redis` exists but is not started.
- The PostGIS extension query against `apexiq-postgres` returned no rows and no confirmation; treat PostGIS as **unavailable**. That container belongs to another project and must not be reused.

### 2.7 Ports already in use

5432 (other project's PostgreSQL), 3000 (unknown process), 11434 (Ollama), 6379 (other project's Redis). Ports 8000 and 5173 were free at check time.

### 2.8 Relevant environment variables

Matched names only: `JAVA_HOME`, `PYTHONIOENCODING`, plus VS Code/Electron internals. **Not set:** any `CUDA*`, `GDAL*`, `PROJ*`, `PG*`, `OLLAMA*`, `HF_*`, `TORCH*`, `CONDA*`, `VIRTUAL_ENV`, map-provider or Cesium variables. The absence of a stray `PROJ_LIB`/`GDAL_DATA` is good: those commonly break pip-installed geospatial wheels.

## 3. Missing dependencies

| Dependency | State | Needed from |
|------------|-------|-------------|
| PostgreSQL + PostGIS | Not available in any form | Phase 1 |
| Project virtual environment and lockfile | Do not exist | Phase 1 |
| SQLAlchemy, Alembic, psycopg, pytest | Not installed | Phase 1 |
| Rasterio / GDAL / PyProj | Not installed | Phase 2 |
| CUDA-enabled PyTorch | Not installed (CPU build only) | Phase 3 |
| Vision model weights for the three pipelines | Not selected, not present | Phase 3 |
| A Qwen model sized for this GPU, approved for aggregation | Not selected | Phase 5 |
| Offline basemap tiles | Not present | Phase 4 |
| PROJ transformation grids | Not present | Phase 2/4, if a grid-based transformation is required |
| Sample/fixture data, labelled validation data | Not present | Phases 2–5 |
| Git repository | Not initialised | Phase 1 |

## 4. Windows compatibility concerns

1. **C: has 8.4 GB free.** The project, the default uv/pip/pnpm caches, the Ollama model store (default location exists under the user profile; `OLLAMA_MODELS` is not set), and very likely Docker Desktop's WSL disk all live on C:. A CUDA PyTorch install alone needs several GB; a PostGIS image, node modules, a Chromium download for end-to-end tests and any model weights will not fit. This is the most immediate practical blocker.
2. **RTX 5060 needs a recent CUDA build of PyTorch.** Compute capability 12.0 is only supported by PyTorch wheels built against CUDA 12.8 or newer. The installed `+cpu` build cannot use the GPU. The exact wheel index tag must be confirmed at install time, then verified with a real tensor operation on the device.
3. **8 GB VRAM, shared with the desktop.** Roughly 6.7 GB was free. A vision model and a local LLM cannot be assumed to fit together; GPU work has to be serialised and models unloaded between stages.
4. **16 GB RAM with about 1.6 GB free.** Many applications were open. Docker Desktop's VM is sized at about 7.6 GiB. Large rasters must be processed in windows/tiles, never loaded whole. The 23 GB Qwen model exceeds VRAM and cannot fit in free RAM either; it was not run and no performance figure is claimed.
5. **Long paths are disabled.** Deep `node_modules` and cache paths can exceed 260 characters. Mitigate with a short project root (for example `D:\geopoc`); enabling long paths needs administrator rights and is a machine setting, so it is only a recommendation.
6. **Port 5432 is taken.** The project database must publish a different host port (5433 proposed).
7. **GDAL on Windows.** There is no system GDAL. Rasterio and PyProj wheels bundle their own GDAL/PROJ, which is the simplest route, but the drivers compiled into those wheels (JPEG2000, NITF) must be checked after installation rather than assumed.
8. **PowerShell 5.1.** No `&&`/`||`; scripts must use 5.1-compatible syntax or be run through `uv run`/`pnpm` scripts.
9. **PDF generation.** WeasyPrint needs GTK/Pango, which is awkward on Windows. See decision D-014.
10. **GPU inside Docker.** An `nvidia` runtime is listed, but GPU passthrough was not tested. The plan keeps all GPU work native on Windows and uses Docker only for the database.

## 5. Recommended setup steps

Nothing here was executed. Each step needs approval because it installs or downloads something (see `phase-1-readiness.md`).

1. Move the project to a short path on D: (for example `D:\geopoc`) and set a data root on D: (for example `D:\geopoc-data`).
2. Point caches at D: for this project: `UV_CACHE_DIR`, pnpm store (`pnpm config set store-dir`), and Playwright browsers (`PLAYWRIGHT_BROWSERS_PATH`).
3. `git init`, add `.gitignore` (data root, weights, `.venv`, `node_modules`, `.env`) and `.gitattributes`.
4. Create an isolated Python 3.11 environment with uv and a committed `uv.lock`. Do not use the global interpreter's packages.
5. Run PostgreSQL + PostGIS from a `postgis/postgis` image through Docker Compose on host port 5433 with a named volume. Confirm where Docker Desktop stores its disk image before pulling.
6. Install frontend dependencies with pnpm and a committed `pnpm-lock.yaml`.
7. Phase 2: add `rasterio`, `pyproj`, `shapely`; record `rasterio.__gdal_version__`, `pyproj.proj_version_str` and the available drivers in `/health`.
8. Phase 3: install a CUDA build of PyTorch from the official PyTorch wheel index into the project environment; verify with `torch.cuda.is_available()`, `torch.cuda.get_device_name(0)` and one GPU tensor operation.
9. Phase 5: pull or select a Qwen model that fits about 6 GB of VRAM; record it in the model manifest with its digest.

## 6. Risks requiring verification

| ID | Risk | How to verify | When |
|----|------|---------------|------|
| E-01 | Insufficient space on C: | Confirm Docker disk-image location; relocate project and caches to D: | Before Phase 1 |
| E-02 | CUDA PyTorch does not support this GPU/driver | Install into the project venv; run a GPU tensor op | Start of Phase 3 |
| E-03 | Chosen vision models exceed about 6 GB VRAM or are too slow | Measure peak VRAM and latency on fixtures | Phase 3 |
| E-04 | Chosen Qwen model does not fit or is too slow | Measure load time, VRAM, tokens/s through Ollama | Start of Phase 5 |
| E-05 | Rasterio wheel lacks a required driver (JPEG2000, NITF) | `rasterio.drivers` / `gdal.GetDriverByName` after install | Phase 2 |
| E-06 | Ollama model store grows on C: | Decide whether to set `OLLAMA_MODELS` to D: (machine-level change, needs approval) | Before Phase 5 |
| E-07 | Docker Desktop not running when the app starts | Health check reports database unavailable with a clear message | Phase 1 |
| E-08 | Memory pressure with large rasters/videos | Windowed reads, streaming decode; test with the largest permitted fixture | Phases 2–3 |
| E-09 | FFmpeg NVDEC decode not functional on this GPU | Test `-hwaccel cuda` on a fixture; CPU decode is the default | Phase 3 (optional) |
| E-10 | Path length failures | Short root on D:, or enable long paths | Phase 1 |

## 7. Not verified

- Whether a CUDA-enabled PyTorch build actually runs on this GPU (only the CPU build is installed).
- Any model's accuracy, latency or memory use. No model was loaded or run.
- Docker GPU passthrough.
- Where Docker Desktop stores its WSL disk image.
- The total size of the Ollama model directory and which drive it is on beyond "the default directory exists".
- Which GDAL drivers the Rasterio wheel ships.
- Internet connectivity, proxy or firewall behaviour.
