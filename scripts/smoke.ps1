# Smoke test of the API. Two modes; the default can never touch the working database.
#
#   .\scripts\smoke.ps1                       SCRATCH mode: resets the scratch database geopoc_smoke, starts its own API on
#                                             port 8020 with stub models, runs read and write checks, then stops that API.
#   .\scripts\smoke.ps1 -Mode working         WORKING mode: read-only checks (GET only) of the app you are running.
#                                             Safe to run any time; it cannot send a write.
#
# Scratch mode uses only: database geopoc_smoke, data folder <data root>\_smoke, port 8020. It refuses to start if any of
# these could be the working ones. The client (python -m geopoc.smoke) also refuses to write unless the API itself reports
# a scratch database. See docs\runbook.md.
param(
    [ValidateSet('scratch', 'working')][string]$Mode = 'scratch',
    [int]$Port = 8020,
    [string]$Url,
    [int]$TimeoutSeconds = 30,
    [int]$StartupTimeoutSeconds = 90
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\env.ps1"
. "$PSScriptRoot\lib\common.ps1"
. "$PSScriptRoot\lib\smoke.ps1"

$settings = Get-GeopocSettings
$apiDir = Join-Path $script:RepoRoot 'services\api'
$clientArguments = @('run', '--no-sync', 'python', '-m', 'geopoc.smoke', '--timeout', [string]$TimeoutSeconds)

function Invoke-SmokeClient {
    param([string]$TargetUrl, [string]$TargetMode)
    $run = Invoke-NativeCommand -FilePath 'uv' -Arguments ($clientArguments + @('--url', $TargetUrl, '--mode', $TargetMode)) -TimeoutSeconds 600 -WorkingDirectory $apiDir
    $run.StdOut.Split("`n") | ForEach-Object { if ($_.Trim()) { Write-Host $_.TrimEnd() } }
    if ($run.StdErr.Trim()) { Write-Host $run.StdErr.Trim() -ForegroundColor Yellow }
    if ($run.TimedOut) { Write-Host "The smoke client timed out." -ForegroundColor Red; return 1 }
    return $run.ExitCode
}

if ($Mode -eq 'working') {
    $plan = Get-SmokeWorkingPlan -Settings $settings -Url $Url
    if ($plan.Failures.Count -gt 0) { $plan.Failures | ForEach-Object { Write-Host "REFUSED: $_" -ForegroundColor Red }; exit 2 }
    Write-Host "WORKING mode: read-only (GET only) against $($plan.Url)"
    exit (Invoke-SmokeClient -TargetUrl $plan.Url -TargetMode 'working')
}

# --- scratch mode ---
$plan = Get-SmokeScratchPlan -Settings $settings -Port $Port
if ($plan.Failures.Count -gt 0) { $plan.Failures | ForEach-Object { Write-Host "REFUSED: $_" -ForegroundColor Red }; exit 2 }
if (@(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count -gt 0) {
    Write-Host "REFUSED: port $Port is already in use. Nothing was started." -ForegroundColor Red
    exit 2
}
if (-not (Test-DockerEngine).Available) { Write-Host "REFUSED: the Docker engine is not running, so the scratch database cannot be created." -ForegroundColor Red; exit 2 }

Write-Host "SCRATCH mode: database $($plan.Database), data folder $($plan.DataRoot), API $($plan.Url)"
$reset = Invoke-NativeCommand -FilePath 'uv' -Arguments @('run', '--no-sync', 'python', '-m', 'geopoc.devtools', 'reset-db') -TimeoutSeconds 300 -Environment $plan.Environment -WorkingDirectory $apiDir
if ($reset.ExitCode -ne 0) { Write-Host "Could not prepare the scratch database: $(Hide-Secret ($reset.StdErr + $reset.StdOut) $settings)" -ForegroundColor Red; exit 1 }
Write-Host "  scratch database recreated and migrated."

$logDir = 'D:\geopoc-cache\smoke'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$saved = @{}
foreach ($key in $plan.Environment.Keys) { $saved[$key] = [Environment]::GetEnvironmentVariable($key); [Environment]::SetEnvironmentVariable($key, $plan.Environment[$key]) }
$apiProcess = $null
try {
    $apiProcess = Start-Process -FilePath 'uv' -ArgumentList @('run', '--no-sync', 'python', '-m', 'geopoc') -WorkingDirectory $apiDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'api.out.log') -RedirectStandardError (Join-Path $logDir 'api.err.log')
}
finally { foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key]) } }

$exit = 1
try {
    $up = Wait-Until -TimeoutSeconds $StartupTimeoutSeconds -IntervalSeconds 2 -Test { $r = Invoke-HttpGet -Url "$($plan.Url)/health" -TimeoutSeconds 3; [bool]($r -and $r.Status -eq 200) }
    if (-not $up) { Write-Host "The scratch API did not become healthy within $StartupTimeoutSeconds s. See $logDir\api.err.log" -ForegroundColor Red }
    else { $exit = Invoke-SmokeClient -TargetUrl $plan.Url -TargetMode 'scratch' }
}
finally {
    if ($apiProcess) { [void](Invoke-NativeCommand -FilePath 'taskkill' -Arguments @('/PID', [string]$apiProcess.Id, '/T', '/F') -TimeoutSeconds 30) }
    Write-Host "Scratch API stopped. (Only the process this script started; the working app was never touched.)"
}
exit $exit
