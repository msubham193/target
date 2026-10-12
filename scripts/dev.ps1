# Starts the local development stack. Everything binds to 127.0.0.1.
#   .\scripts\dev.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot\env.ps1"

if (-not (Test-Path "$root\.env")) { throw "Missing .env. Copy .env.example to .env and set GEOPOC_DB_PASSWORD." }

Write-Host "Starting PostgreSQL + PostGIS (127.0.0.1:5433)..."
docker compose --env-file "$root\.env" -f "$root\infra\docker-compose.yml" up -d --wait
if ($LASTEXITCODE -ne 0) { throw "The database did not start. Is Docker Desktop running?" }

Write-Host "Applying database migrations..."
Push-Location "$root\services\api"
uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Migrations failed." }
Pop-Location

Write-Host "Starting API (http://127.0.0.1:8000) and web app (http://127.0.0.1:5173) in new windows..."
Start-Process powershell -ArgumentList '-NoExit', '-Command', "Set-Location '$root\services\api'; uv run python -m geopoc"
Start-Process powershell -ArgumentList '-NoExit', '-Command', "Set-Location '$root\apps\web'; pnpm run dev"
Write-Host "Open http://127.0.0.1:5173"
