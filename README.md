# Geospatial PoC

Local-first workbench for general-purpose geospatial observation, imagery analysis, evidence review and reporting. Three independent input pipelines are planned: ground photographs, overhead rasters (including GeoTIFF) and video.

The application does not make operational targeting or engagement decisions and must not be extended to do so.

**Status: Phase 2 of 6.** Workspace profile, dashboard, projects, offline CRS search, service health, and asset ingestion (upload, validation, checksums, duplicate detection, metadata) for all three kinds of input. No analysis, map, reports or 3D yet. See [docs/phase-2-report.md](docs/phase-2-report.md) and [docs/ingestion.md](docs/ingestion.md).

## Requirements

Windows, PowerShell 5.1+, Git, Python 3.11, [uv](https://docs.astral.sh/uv/), Node.js with pnpm, Docker Desktop, FFmpeg (`ffmpeg` and `ffprobe` on PATH, for video), Microsoft Edge (for browser tests).

## First-time setup

```powershell
Copy-Item .env.example .env        # then set GEOPOC_DB_PASSWORD in .env (git-ignored)
New-Item -ItemType Directory -Force D:\geopoc-data, D:\geopoc-cache
. .\scripts\env.ps1                # keeps package caches on D:
Set-Location services\api; uv sync; Set-Location ..\..
Set-Location apps\web; pnpm install; Set-Location ..\..
```

## Run

```powershell
.\scripts\dev.ps1                  # database, migrations, API on 127.0.0.1:8000, web on 127.0.0.1:5173
```

Open <http://127.0.0.1:5173>. All services bind to loopback only.

## Check

```powershell
.\scripts\check.ps1                # lint, format, type-check, unit, contract, migration and browser tests
```

Tests use scratch databases (`geopoc_test`, `geopoc_e2e`) and never touch the working database.

## Things to know

- **Local profiles are not authentication.** A profile is a name recorded in the audit history. There is no access control; do not expose the application beyond this computer.
- **Nothing is sent off the machine.** Online sources are off, PROJ network access is off, and no cloud AI service is used.
- **Uploaded originals** are stored unmodified and read-only under `GEOPOC_DATA_ROOT` (default `D:/geopoc-data`), named by checksum.
- **Dependency pins.** `psycopg[binary]`, `pyproj` and `rasterio` are pinned because Windows Smart App Control on the development machine blocks the native libraries in their newest releases. See the comment in `services/api/pyproject.toml`.
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
models/          model manifest (empty: no model selected yet)
infra/           Docker Compose for PostgreSQL + PostGIS
scripts/         dev and check scripts
docs/            approved specification and architecture documents
```
