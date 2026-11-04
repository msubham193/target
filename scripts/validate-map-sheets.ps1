# Checks georeferenced map sheets against independent reference points, offline.
#
#   .\scripts\validate-map-sheets.ps1 -Manifest C:\path\to\manifest.json [-OutDir C:\path\to\reports]
#
# Writes map-sheet-validation.json and map-sheet-validation.md into -OutDir (default: next to the manifest). It reads the
# sheets and writes only those two reports. It does not open the database, does not read .env (this script deliberately does
# not load scripts\env.ps1), does not start or stop anything and sends nothing anywhere: PROJ's network is forced off.
#
# Exit status: 0 every real sheet validated, 1 a real sheet failed or the manifest is invalid, 3 NOT VALIDATED (the manifest
# has no real, authorised sample). It never reports VALIDATED for synthetic sheets. See docs\map-sheet-validation.md.
param(
    [Parameter(Mandatory = $true)][string]$Manifest,
    [string]$OutDir
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$apiDir = Join-Path $repoRoot 'services\api'
$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
if (-not $OutDir) { $OutDir = Split-Path -Parent $manifestPath }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$env:PROJ_NETWORK = 'OFF'
Push-Location $apiDir
try {
    & uv run --no-sync python -m geopoc.validation.mapsheets `
        --manifest $manifestPath `
        --json (Join-Path $OutDir 'map-sheet-validation.json') `
        --markdown (Join-Path $OutDir 'map-sheet-validation.md')
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
switch ($code) {
    0 { Write-Host "Every real sheet validated. Reports are in $OutDir." -ForegroundColor Green }
    3 { Write-Host "NOT VALIDATED: no real, authorised sample sheet was checked. Reports are in $OutDir." -ForegroundColor Yellow }
    default { Write-Host "Validation failed or the manifest is invalid (exit $code). See $OutDir." -ForegroundColor Red }
}
exit $code
