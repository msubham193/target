# Runs every Phase 1 check: lint, format, type-check, unit, contract, migration and browser tests.
#   .\scripts\check.ps1
# Requires the database container to be running (.\scripts\dev.ps1 starts it).
$root = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot\env.ps1"
$results = @()

function Step($name, $dir, [scriptblock]$command) {
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    Push-Location $dir
    & $command
    $ok = ($LASTEXITCODE -eq 0)
    Pop-Location
    $script:results += [pscustomobject]@{ Check = $name; Result = $(if ($ok) { 'PASS' } else { 'FAIL' }) }
}

$api = "$root\services\api"
$web = "$root\apps\web"

Step 'backend lint (ruff check)'        $api { uv run ruff check . }
Step 'backend format (ruff format)'     $api { uv run ruff format --check . }
Step 'backend tests (pytest)'           $api { uv run pytest -q }
Step 'frontend type-check (tsc)'        $web { pnpm run typecheck }
Step 'frontend unit tests (vitest)'     $web { pnpm run test }
Step 'frontend build (vite)'            $web { pnpm exec vite build }
Step 'browser end-to-end (playwright)'  $web { pnpm run e2e }

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize
if ($results.Result -contains 'FAIL') { exit 1 }
