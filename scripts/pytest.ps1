# Runs the backend tests:   .\scripts\pytest.ps1 -q          or      .\scripts\pytest.ps1 tests\test_health.py -q
#
# On this machine Windows Smart App Control can block a native library (jsonschema_rs) that the Schemathesis pytest
# plugin loads at start-up; pytest then cannot even start. This wrapper checks for that first. If the library is blocked it
# disables ONLY that plugin for this run, says so, and tests\test_contract.py is reported as SKIPPED with the reason. The
# pure-Python contract tests (test_openapi_contract.py, test_openapi_drift.py) still run. Nothing is bypassed or
# unblocked. Tests use the scratch database geopoc_test and never the working database.
# Deliberately no param() block: a declared parameter would make PowerShell read pytest's own -W, -p, -q ... as its common
# parameters (for example "-W" is ambiguous with -WarningAction). Every argument is passed to pytest unchanged.
$PytestArguments = @($args)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\env.ps1"
. "$PSScriptRoot\lib\common.ps1"

$apiDir = Join-Path $script:RepoRoot 'services\api'
$probe = Invoke-NativeCommand -FilePath 'uv' -Arguments @('run', '--no-sync', 'python', '-c', 'import jsonschema_rs') -TimeoutSeconds 120 -WorkingDirectory $apiDir
if ($probe.ExitCode -ne 0) {
    $reason = (($probe.StdErr -split "`n") | Where-Object { $_.Trim() } | Select-Object -Last 1).Trim()
    Write-Host "NOTE: the Schemathesis plugin cannot load on this machine ($reason)." -ForegroundColor Yellow
    Write-Host "      Running with -p no:schemathesis. tests\test_contract.py will be reported as SKIPPED; the pure-Python contract tests still run." -ForegroundColor Yellow
    # Passed as an argument, not through PYTEST_ADDOPTS: setting that variable here would outlive this script when it is run
    # in the current session, and would be inherited by every later pytest run.
    $PytestArguments = @('-p', 'no:schemathesis') + $PytestArguments
}
if ($env:PYTEST_ADDOPTS) {
    Write-Host "NOTE: PYTEST_ADDOPTS is set in this session ('$($env:PYTEST_ADDOPTS)') and will be applied to this run. Clear it if that is not intended." -ForegroundColor Yellow
}
Push-Location $apiDir
try {
    # Output on stderr is normal for test runs; it must not become a PowerShell error, so the 'Stop' setting is lifted here.
    $ErrorActionPreference = 'Continue'
    # python -m: Smart App Control can also block the pytest.exe launcher.
    & uv run --no-sync python -m pytest @PytestArguments
    $code = $LASTEXITCODE
}
finally { Pop-Location }
exit $code
