# Starts the local development stack, or completes a partial start. Safe to run more than once: a service that is
# already running is never started a second time. Everything binds to 127.0.0.1.
#
#   .\scripts\dev.ps1                  start what is missing, then wait until it is healthy
#   .\scripts\dev.ps1 -StartDocker     also start Docker Desktop if its engine is not running (after a reboot)
#   .\scripts\dev.ps1 -NoWait          start and return at once
#   .\scripts\dev.ps1 -SkipWeb         API and worker only
#
# This script never applies database migrations. It reports when the database is behind the code and tells you to
# run .\scripts\migrate.ps1 (which needs a verified backup). See docs\runbook.md.
param(
    [switch]$StartDocker,
    [switch]$NoWait,
    [switch]$SkipWeb,
    [int]$HealthTimeoutSeconds = 120,
    [int]$DetectorTimeoutSeconds = 300
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\env.ps1"
. "$PSScriptRoot\lib\common.ps1"
. "$PSScriptRoot\lib\backup.ps1"
. "$PSScriptRoot\lib\startup.ps1"

$settings = Get-GeopocSettings
if (-not $settings.EnvFileFound) { throw "Missing .env. Copy .env.example to .env and set GEOPOC_DB_PASSWORD." }
if (-not $settings.HasDbPassword) { throw "GEOPOC_DB_PASSWORD is empty in .env." }
if (@('127.0.0.1', 'localhost', '::1') -notcontains $settings.ApiHost) { throw "GEOPOC_API_HOST is '$($settings.ApiHost)'. This application is local-only: use 127.0.0.1." }

# --- 1. Docker engine ---
$docker = Test-DockerEngine
if (-not $docker.Available) {
    if (-not $StartDocker) {
        Write-Host "The Docker engine is not running." -ForegroundColor Yellow
        Write-Host "Start Docker Desktop and wait until it says it is running, or run:  .\scripts\dev.ps1 -StartDocker" -ForegroundColor Yellow
        exit 1
    }
    $desktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path -LiteralPath $desktop)) { throw "Docker Desktop was not found at $desktop. Start it by hand." }
    Write-Host "Starting Docker Desktop (this can take a minute or two)..."
    Start-Process $desktop
    if (-not (Wait-Until -Test { (Test-DockerEngine).Available } -TimeoutSeconds 240 -IntervalSeconds 5)) {
        Write-Host "Docker did not become ready within 4 minutes. Open Docker Desktop and check it, then run this script again." -ForegroundColor Red
        exit 1
    }
}

# --- 2. Database container ---
Write-Host "Starting PostgreSQL + PostGIS (127.0.0.1:$($settings.DbPort))..."
$compose = Invoke-NativeCommand -FilePath 'docker' -Arguments @('compose', '--env-file', (Join-Path $script:RepoRoot '.env'), '-f', (Join-Path $script:RepoRoot 'infra\docker-compose.yml'), 'up', '-d', '--wait') -TimeoutSeconds 240
($compose.StdOut + $compose.StdErr).Split("`n") | ForEach-Object { if ($_.Trim()) { Write-Host "  $($_.Trim())" } }
if ($compose.ExitCode -ne 0) { Write-Host "The database container did not start (exit $($compose.ExitCode))." -ForegroundColor Red; exit 1 }

# --- 3. Migration state: reported, never applied here ---
$headEnvironment = @{ GEOPOC_DB_NAME = $settings.DbName }
$head = Get-MigrationHead -ApiDirectory (Join-Path $script:RepoRoot 'services\api') -Environment $headEnvironment
$current = Get-DatabaseRevision -Settings $settings -Database $settings.DbName
$migration = Compare-MigrationRevision -Database $current -Head $head
switch ($migration) {
    'current' { Write-Host "Database schema is current (revision $current)." }
    'behind' { Write-Host "The database is at revision $current but the code expects $head. Nothing was changed. Take a verified backup and run  .\scripts\migrate.ps1 -Apply  (see docs\runbook.md)." -ForegroundColor Yellow }
    'ahead' { Write-Host "The database (revision $current) is NEWER than this code (revision $head). Check that you are on the right branch before using the application." -ForegroundColor Yellow }
    default { Write-Host "Could not compare the database revision ($current) with the code ($head); continuing." -ForegroundColor Yellow }
}

# --- 4. Application services: only what is missing ---
$state = Get-ServiceState -Settings $settings
$plan = Get-StartPlan -State $state -SkipWeb:$SkipWeb
foreach ($warning in $plan.Warnings) { Write-Host $warning -ForegroundColor Yellow }
$windows = Read-ServiceWindows
if ($plan.StartApi) { Write-Host "Starting the API (http://127.0.0.1:$($settings.ApiPort))..."; $windows['api'] = Start-ServiceWindow -Name api } else { Write-Host "API already running; not started again." }
if ($plan.StartWorker) { Write-Host "Starting the worker..."; $windows['worker'] = Start-ServiceWindow -Name worker } else { Write-Host "Worker already running; not started again." }
if ($plan.StartWeb) { Write-Host "Starting the web app (http://127.0.0.1:$($settings.WebPort))..."; $windows['web'] = Start-ServiceWindow -Name web } elseif (-not $SkipWeb) { Write-Host "Web app already running; not started again." }
if ($plan.StartApi -or $plan.StartWorker -or $plan.StartWeb) { Save-ServiceWindows -WindowIds $windows }

if ($NoWait) { Write-Host "Started. Check health with:  .\scripts\smoke.ps1 -Mode working"; exit 0 }

# --- 5. Wait until healthy ---
$apiUrl = "http://127.0.0.1:$($settings.ApiPort)"
$healthy = Wait-Until -TimeoutSeconds $HealthTimeoutSeconds -IntervalSeconds 3 -Test {
    $r = Invoke-HttpGet -Url "$apiUrl/health" -TimeoutSeconds 4
    if (-not $r -or $r.Status -ne 200) { return $false }
    $body = $r.Content | ConvertFrom-Json
    return ($body.status -eq 'ok' -and $body.worker.running)
}
$summary = @()
if ($healthy) {
    $body = (Invoke-HttpGet -Url "$apiUrl/health").Content | ConvertFrom-Json
    $summary += [pscustomobject]@{ Service = 'API'; State = 'healthy'; Detail = "schema revision $($body.database.schema_revision), worker running" }
}
else {
    $summary += [pscustomobject]@{ Service = 'API'; State = 'NOT HEALTHY'; Detail = "no healthy answer from $apiUrl/health within $HealthTimeoutSeconds s. Check the API and worker windows." }
}
if (-not $SkipWeb) {
    $webOk = Wait-Until -TimeoutSeconds 60 -IntervalSeconds 2 -Test { $r = Invoke-HttpGet -Url "http://127.0.0.1:$($settings.WebPort)/" -TimeoutSeconds 4; [bool]($r -and $r.Status -eq 200) }
    $summary += [pscustomobject]@{ Service = 'Web app'; State = $(if ($webOk) { 'healthy' } else { 'NOT HEALTHY' }); Detail = "http://127.0.0.1:$($settings.WebPort)" }
}
if ($healthy) {
    $detector = $null
    Wait-Until -TimeoutSeconds $DetectorTimeoutSeconds -IntervalSeconds 5 -Test {
        $r = Invoke-HttpGet -Url "$apiUrl/health/models" -TimeoutSeconds 5
        $script:detector = Get-DetectorStatus -ModelHealth $(if ($r) { $r.Content | ConvertFrom-Json } else { $null })
        return ($script:detector.State -notin @('warming-up', 'api-unreachable'))
    } | Out-Null
    $summary += [pscustomobject]@{ Service = 'Detector'; State = $script:detector.State; Detail = $script:detector.Detail }
}
Write-Host ""
$summary | Format-Table -AutoSize -Wrap
if (($summary | Where-Object { $_.State -eq 'NOT HEALTHY' }).Count -gt 0) { exit 1 }
