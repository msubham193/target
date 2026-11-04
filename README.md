# INT and TGT Development

Local-first workbench for general-purpose geospatial observation, imagery analysis, evidence review and reporting. Three independent input pipelines are planned: ground photographs, overhead rasters (including GeoTIFF) and video.

The application does not make operational targeting or engagement decisions and must not be extended to do so.

**Status: in development.** Working today: projects and areas of interest; file upload (photographs, GeoTIFF rasters, video, OSM/ESM map sheets); a background worker with local object detection on photographs and sampled video frames; a review workflow for findings; a map with an optional online satellite basemap; a satellite-image viewer; 3D terrain from elevation rasters; place search; findings reports and exports. Planned, not built: AI analysis of rasters, custom CRS definitions, online data sources. NDVI Studio is hidden from the app (its code is kept, and its database migration `0010` is not yet applied to the author's working database). Phase reports are in [docs/](docs/).

## Quick start (from GitHub)

This is a **Windows** application. It runs entirely on your own computer.

```powershell
# 1. Get the code
git clone https://github.com/msubham193/target.git
Set-Location target

# 2. Install the software listed under "Requirements" below (Docker Desktop must be running)

# 3. One-time setup (details in "First-time setup" below)
Copy-Item .env.example .env              # open .env and set GEOPOC_DB_PASSWORD to any password you choose
New-Item -ItemType Directory -Force D:\geopoc-data, D:\geopoc-cache
. .\scripts\env.ps1                      # run this in every new terminal
Set-Location services\api; uv sync --group ml; Set-Location ..\..
Set-Location services\api; uv run python -m geopoc.devtools fetch-model fasterrcnn_resnet50_fpn_v2@coco_v1; Set-Location ..\..
Set-Location apps\web; pnpm install; Set-Location ..\..

# 4. Start everything (database container, API on 8000, worker, web app on 5173)
.\scripts\dev.ps1 -StartDocker
```

5. **Create the database tables (first time only).** `dev.ps1` never changes the database by itself and will say the schema is missing. Take a verified backup, then migrate: `.\scripts\backup-db.ps1 -Verify`, then `.\scripts\migrate.ps1 -Apply`. If the backup step refuses on an empty database, follow [docs/runbook.md](docs/runbook.md). Then run `.\scripts\dev.ps1` again.
6. Open <http://127.0.0.1:5173>.
7. **Sign in** with the demo account `demo@intgt.dev` / `Demo@1234` (the sign-in page shows it too), then create a profile (just a name for the audit history) and a project.

> The demo sign-in is a placeholder that protects nothing: its details are in the page's code. Do not expose the app beyond your own computer.

### Optional: the online satellite basemap and place search (Mapbox)

Without this, everything works offline with a plain map. To show Mapbox satellite imagery, a dark satellite picture on the sign-in page and dashboard header, and place-name suggestions when creating a project:

1. Make a free Mapbox account and create a **public** access token (it starts with `pk.`). Never use a secret token (`sk.`); the app refuses it.
2. Create the file `apps/web/.env.local` with one line: `VITE_MAPBOX_ACCESS_TOKEN=pk.your-token-here` (this file is git-ignored; never commit a token).
3. Restart the web app: `.\scripts\stop.ps1 -Only web`, then `.\scripts\dev.ps1`.

This is the one feature that sends map views and typed place names to a third party. See [docs/mapbox-satellite.md](docs/mapbox-satellite.md).

### Where to find things in the app

| Screen | What it is for |
|---|---|
| Project Dashboard | your projects, counts and recent activity |
| New Project | name a project; optionally add an area of interest (with place search) |
| Add Files | upload photographs, GeoTIFF rasters, video and map sheets |
| Map | your georeferenced images and located photographs on a map; measure, annotate, pin |
| Photos / Photo Analysis | look at photographs, confirm where they were taken, run the detector, review and draw your own marks |
| Satellite Images | view a satellite or aerial image; true colour, grayscale, brightness, contrast; technical tools under Advanced inspection |
| Videos | analyse a bounded sample of video frames |
| 3D Terrain | elevation rasters drawn as terrain |
| Findings / Reports | review every finding; the findings report and CSV/JSON/GeoJSON exports |
| Settings | models, coordinate systems, network use, diagnostics |

## Requirements

Windows, PowerShell 5.1+, Git, Python 3.11, [uv](https://docs.astral.sh/uv/), Node.js with pnpm, Docker Desktop, FFmpeg (`ffmpeg` and `ffprobe` on PATH, for video), Microsoft Edge (for browser tests).

## First-time setup

The same steps as the quick start, with the reasons. The commands assume drive `D:` for caches and data; see [docs/setup-on-another-device.md](docs/setup-on-another-device.md) if you have no `D:`.

```powershell
Copy-Item .env.example .env        # then set GEOPOC_DB_PASSWORD in .env (git-ignored)
New-Item -ItemType Directory -Force D:\geopoc-data, D:\geopoc-cache
. .\scripts\env.ps1                # keeps caches and the Python environment on D: (run in every new terminal)
Set-Location services\api; uv sync --group ml; Set-Location ..\..      # --group ml adds PyTorch (about 4 GB); omit to skip analysis
Set-Location services\api; uv run python -m geopoc.devtools fetch-model fasterrcnn_resnet50_fpn_v2@coco_v1; Set-Location ..\..
Set-Location apps\web; pnpm install; Set-Location ..\..
```

## Run

```powershell
.\scripts\dev.ps1                  # database container, then the API (8000), worker and web app (5173) that are not already running; waits until healthy
.\scripts\dev.ps1 -StartDocker     # the same, starting Docker Desktop first (after a reboot)
.\scripts\stop.ps1                 # stops the API, worker and web app (the database keeps running); -Only api|worker|web, -DryRun
.\scripts\smoke.ps1 -Mode working  # read-only health and consistency checks of the running app (GET only)
.\scripts\backup-db.ps1 -Verify    # database backup with a test restore; required before a migration or a reset
.\scripts\migrate.ps1              # dry run: database revision versus the code; add -Apply (needs a verified backup) to migrate
```

Open <http://127.0.0.1:5173>. All services bind to loopback only. `dev.ps1` is safe to run twice and **never applies migrations**: it tells you when the database is behind the code. After a reboot, Docker Desktop must be running first. Start-up, recovery, backups and restores are described step by step in [docs/runbook.md](docs/runbook.md).

`.\scripts\reset-working-data.ps1` is **not a routine cleanup**. It is a guarded tool that erases application data: it does a dry run unless you pass `-Execute`, refuses the working database without a separate `-AuthorizeWorkingData`, a verified recent backup, stopped services and a typed confirmation phrase, ignores `-Force` for the working database, and moves files to a quarantine folder instead of deleting them. Read the runbook before using it.

## Check

```powershell
.\scripts\check.ps1                # lint, format, script tests, backend, frontend and browser tests
.\scripts\pytest.ps1 -q            # backend tests only
.\scripts\smoke.ps1                # scratch-mode smoke test: its own database (geopoc_smoke), its own API on port 8020
```

Tests use scratch databases (`geopoc_test`, `geopoc_e2e`, `geopoc_smoke`, `geopoc_script_test`) and never touch the working database. If Windows Smart App Control blocks the Schemathesis plugin, `pytest.ps1` disables only that plugin and `tests/test_contract.py` is reported as skipped with the reason; the pure-Python contract tests (`test_openapi_contract.py`, `test_openapi_drift.py`) always run.

## Things to know

- **Local profiles are not authentication.** A profile is a name recorded in the audit history. There is no access control; do not expose the application beyond this computer.
- **Nothing is sent off the machine.** Online sources are off, PROJ network access is off (forced `OFF` for pyproj and for GDAL when the package is imported; `/health` reports both PROJ copies), and no cloud AI service is used.
- **Optional online satellite basemap.** Mapbox Standard Satellite can be drawn under the map layers if a Mapbox public token is set in `apps/web/.env.local`; it is off by default and it is the one feature that sends map views to a third party. See [docs/mapbox-satellite.md](docs/mapbox-satellite.md).
- **Datum shifts worse than 10 m need your acknowledgement.** A transformation with a documented accuracy over 10 m, or none stated, is not used until it is acknowledged; ballpark ones never are. See [docs/transformation-acknowledgement.md](docs/transformation-acknowledgement.md). A raster georeferenced by its own file that needs one stays blocked.
- **No real OSM or ESM map sheet has been validated.** `.\scripts\validate-map-sheets.ps1` checks sheets against independent reference points offline and says NOT VALIDATED until real, authorised samples are supplied ([docs/map-sheet-validation.md](docs/map-sheet-validation.md)).
- **The worker must be running** for previews and analysis; `dev.ps1` starts it. Without it, jobs wait in the queue.
- **Model output is unevaluated.** Scores are not probabilities and every finding needs review. See `models/cards/`.
- **Uploaded originals** are stored unmodified and read-only under `GEOPOC_DATA_ROOT` (default `D:/geopoc-data`), named by checksum.
- **Dependency pins.** `psycopg[binary]`, `pyproj` and `rasterio` are pinned because Windows Smart App Control on the development machine blocks the native libraries in their newest releases. `torch` and `torchvision` are pinned to the CUDA build verified on the RTX 5060. See the comments in `services/api/pyproject.toml`; load-test before raising any of them.
- **API types.** After changing the API, regenerate the client types:
  ```powershell
  Set-Location services\api
  uv run python -c "import json, pathlib; from geopoc.api.main import app; pathlib.Path('openapi.json').write_text(json.dumps(app.openapi(), indent=2) + '\n', encoding='utf-8')"
  Set-Location ..\..\apps\web; pnpm run gen:api
  ```

## Layout

```
apps/web/        React + TypeScript + Vite + Tailwind
services/api/    FastAPI, SQLAlchemy, Alembic migrations, tests
models/          model manifest and model cards (weights are not in the repository)
infra/           Docker Compose for PostgreSQL + PostGIS
scripts/         start, stop, backup, migrate, smoke, guarded reset and check scripts (scripts/lib: shared code; scripts/tests: script tests)
docs/runbook.md  how to start, recover, back up and restore
docs/            approved specification and architecture documents
```
