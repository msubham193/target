# Runs every check: lint, format, script tests, backend tests, type-check, unit tests, build and browser tests.
#   .\scripts\check.ps1
# Requires the database container to be running (.\scripts\dev.ps1 starts it). Backend and browser tests use the scratch
# databases geopoc_test and geopoc_e2e; the script tests use geopoc_script_test. None of them touches the working database.
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

Step 'backend lint (ruff check)'        $api { uv run --no-sync ruff check . }
Step 'backend format (ruff format)'     $api { uv run --no-sync ruff format --check . }
Step 'script tests (guards, backup, reset)' $root { powershell -NoProfile -File "$root\scripts\tests\run-script-tests.ps1" }
# pytest.ps1 handles a Schemathesis plugin blocked by Smart App Control: that one test is then SKIPPED with a visible reason (-rs).
Step 'backend tests (pytest)'           $api { & "$root\scripts\pytest.ps1" -q }
Step 'frontend type-check (tsc)'        $web { pnpm run typecheck }
Step 'frontend unit tests (vitest)'     $web { pnpm run test }
Step 'frontend build (vite)'            $web { pnpm exec vite build }
Step 'browser end-to-end (playwright)'  $web { pnpm run e2e }

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize
if ($results.Result -contains 'FAIL') { exit 1 }
