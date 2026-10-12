# Phase 1 Readiness — Geospatial PoC

Date: 2026-10-12.

> **Superseded.** The blockers below were resolved and Phase 1 was completed on 2026-10-12. See [../phase-1-report.md](../phase-1-report.md). The project root is the workspace folder, not `D:\geopoc`; the commands in section 5 are historical.

## Verdict

**Not ready to start yet. Ready once five approvals are given.** There is no technical obstacle to Phase 1 (app shell, navigation, Dashboard, project wizard). The blockers are a disk-space problem on the system drive and decisions that only the project owner can make.

Phase 1 does not need a GPU, a vision model, a Qwen model, sample data or the source presentation.

## 1. Checklist

| Item | State | Evidence |
|------|-------|----------|
| Specification read in full | Done | 415 extracted lines covering V2 §1–14 and V3 §1–10; SHA-256 recorded |
| Requirements extracted and traced | Done | [requirements-traceability.md](requirements-traceability.md) |
| Architecture proposed | Done | [implementation-blueprint.md](implementation-blueprint.md) |
| Environment inspected | Done | [environment-assessment.md](environment-assessment.md) |
| Git available | Yes | 2.53.0 |
| Python + uv available | Yes | 3.11.9, uv 0.10.7 |
| Node + pnpm available | Yes | 24.14.0, pnpm 10.33.0 |
| Docker available and running | Yes | 29.2.1, Compose v5.0.2 |
| PostgreSQL + PostGIS available | **No** | No native install; no PostGIS image; port 5432 in use by another project |
| Enough disk space where the project lives | **No** | C: has 8.4 GB free; D: has 374 GB free |
| Git repository initialised | **No** | Directory is not a repository |
| Project Python environment | **No** | To be created in Phase 1 |
| Authentication approach decided | **No** | Open in the specification (V2 §14) |
| Permission to install and download | **Not yet given** | Phase 0 forbade both |

## 2. Blocking issues for Phase 1

| ID | Blocker | What is needed | Reference |
|----|---------|----------------|-----------|
| B-1 | The project sits on C: with 8.4 GB free | Approve moving the repository to a short path on D: (for example `D:\geopoc`), a `DATA_ROOT` on D:, and caches redirected to D: | D-004, E-01 |
| B-2 | No PostGIS | Approve running `postgis/postgis` through Docker Compose on host port 5433, and confirm Docker Desktop's disk image is not on the full drive (or approve moving it) | D-003 |
| B-3 | Authentication model undecided, and Screen 1 is in Phase 1 | Approve local single-workspace mode (loopback only, named profile, optional local accounts later), or state the alternative | D-018, U-009 |
| B-4 | Phase 0 rules prohibit changes | Explicit approval to initialise git, create the project structure, install Python and Node packages, and pull the PostGIS image | — |
| B-5 | Document version ambiguity | Confirm that the supplied file is the approved Version 3.0 even though its cover page says "Revision 2.0" and its footer says "Version 2.0" | S-01 |

## 3. Not blocking Phase 1, but blocking later phases

| Needed before | Item | Reference |
|---------------|------|-----------|
| Phase 2 | Ruling on the two observation schemas and the two review-status vocabularies | D-008, D-009, S-02–S-04 |
| Phase 2 | Duplicate-upload policy | D-013 |
| Phase 2 | Supported formats and maximum file sizes | U-003 |
| Phase 3 | Model family per pipeline, supported classes, labelled validation data | U-001, U-015 |
| Phase 3 | Calibration, thresholds, review policy, image-quality criteria | U-004, U-005 |
| Phase 3 | Video sampling interval; whether cross-frame association is in the PoC | U-010 |
| Phase 3 | CUDA-enabled PyTorch verified on the RTX 5060 | E-02 |
| Phase 4 | Offline basemap source and licence | U-013 |
| Phase 4 | CRS list, transformation grids, coordinate tolerances; reviewer roles | U-006, U-009 |
| Phase 5 | Qwen model for aggregation that fits 8 GB of VRAM | U-002, E-04 |
| Phase 5 | Report template; PDF renderer; online discovery in or out | U-008, D-014, U-014 |
| Phase 6 | Supported 3D formats | U-012 |
| Any DSM claim | Authoritative Defence Series Map definition | U-007 |
| Traceability sign-off | The source presentation, to audit slide references | U-016 |

## 4. Required inputs from the project owner

1. Answers to B-1 to B-5.
2. For Phase 2 onwards: a few representative sample files per modality that may be used as test fixtures, with any handling restrictions stated. Small synthetic fixtures will be generated in test code where real samples are not available.
3. For Phase 3: candidate models or constraints on them (licence, source, classes), and whatever labelled data exists.
4. If DSM matters for the demo: the authoritative definition and the name of the reviewer who will validate it.

## 5. Commands the developer will run in Phase 1

Not executed in Phase 0. Shown for Windows PowerShell 5.1 (no `&&`). Paths assume B-1 is approved as `D:\geopoc`; adjust if a different location is chosen. Package versions are resolved and locked at install time, not assumed here.

**Check Docker's storage location before pulling anything**

```powershell
docker system df
Get-ChildItem "$env:LOCALAPPDATA\Docker\wsl" -Recurse -Filter *.vhdx -ErrorAction SilentlyContinue | Select-Object FullName, @{n='GB';e={[math]::Round($_.Length/1GB,1)}}
```

**Relocate and initialise**

```powershell
New-Item -ItemType Directory -Path D:\geopoc
New-Item -ItemType Directory -Path D:\geopoc-data
Copy-Item -Path C:\Users\HP\Desktop\target\docs -Destination D:\geopoc\docs -Recurse
Set-Location D:\geopoc
git init
```

**Redirect caches for this project (session-level; persist them in a dev script)**

```powershell
$env:UV_CACHE_DIR = 'D:\geopoc-cache\uv'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\geopoc-cache\playwright'
pnpm config set store-dir D:\geopoc-cache\pnpm --location project
```

**Database**

```powershell
docker compose -f infra\docker-compose.yml up -d
docker compose -f infra\docker-compose.yml ps
docker compose -f infra\docker-compose.yml exec db psql -U geopoc -d geopoc -c "select postgis_full_version();"
```

**Backend**

```powershell
Set-Location D:\geopoc\services\api
uv venv --python 3.11
uv sync
uv run alembic upgrade head
uv run pytest
uv run uvicorn geopoc.api.main:app --host 127.0.0.1 --port 8000
```

**Frontend**

```powershell
Set-Location D:\geopoc\apps\web
pnpm install
pnpm run typecheck
pnpm run test
pnpm run dev
```

**End-to-end (downloads a browser once)**

```powershell
pnpm exec playwright install chromium
pnpm exec playwright test
```

**Health check**

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/models
```

**Later phases — listed so the dependencies are visible, not for Phase 1**

```powershell
# Phase 2: confirm the geospatial stack the wheels actually ship
uv run python -c "import rasterio, pyproj; print(rasterio.__gdal_version__, pyproj.proj_version_str); print(sorted(rasterio.drivers.raster_driver_extensions().items()))"

# Phase 3: after installing a CUDA build of PyTorch from the official wheel index, verify on the device
uv run python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available()); print(torch.cuda.get_device_name(0)); print((torch.ones(8, device='cuda') * 2).sum().item())"

# Phase 5: confirm the approved aggregation model is present and local
ollama list
ollama show <approved-qwen-tag>
```

## 6. What Phase 1 will and will not deliver

Will: repository and tooling; PostGIS running; `/health` and `/health/models`; project create/list/search/open/edit; offline CRS search; Login/Local Workspace, Dashboard, New Project & Location, Settings shell; the route skeleton for the other screens with honest "not available yet" states.

Will not: uploads, jobs, pipelines, observations, map layers, aggregation, reports, 3D.

Exit criterion (specification V2 §13): a project can be created and reopened.

## 7. Recommended next step

Reply with decisions on B-1 to B-5. On approval, Phase 1 starts with the Docker storage check and the relocation to D:.
