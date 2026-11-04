# Start-up, status and migration-state helpers. Dot-source after common.ps1.
# The decision functions here are pure (they take data and return data) so they can be tested without starting anything.

$script:RunDir = if ($env:GEOPOC_RUN_DIR) { $env:GEOPOC_RUN_DIR } else { 'D:\geopoc-cache\run' }
$script:PidFile = Join-Path $script:RunDir 'services.json'

function Get-StartPlan {
    # Which services still need starting, given what is already running. Running ones are never started again.
    param([Parameter(Mandatory)]$State, [switch]$SkipWeb)
    $api = @($State.ApiProcessIds).Count -gt 0 -or $State.ApiListening
    $worker = @($State.WorkerProcessIds).Count -gt 0
    $web = @($State.WebProcessIds).Count -gt 0 -or $State.WebListening
    $plan = [pscustomobject]@{ StartApi = (-not $api); StartWorker = (-not $worker); StartWeb = ((-not $web) -and (-not $SkipWeb)); Warnings = @() }
    # Each service normally shows two python processes (the environment's launcher and the interpreter).
    if (@($State.ApiProcessIds).Count -gt 2) { $plan.Warnings += "More than one API appears to be running ($(@($State.ApiProcessIds).Count) python processes). Run scripts\stop.ps1 -Only api, then start again." }
    if (@($State.WorkerProcessIds).Count -gt 2) { $plan.Warnings += "More than one worker appears to be running ($(@($State.WorkerProcessIds).Count) python processes). Two workers share the GPU and the queue. Run scripts\stop.ps1 -Only worker, then start again." }
    return $plan
}

function Compare-MigrationRevision {
    # 'current' | 'behind' | 'ahead' | 'unknown'. Revisions are zero-padded numbers such as 0008.
    param([string]$Database, [string]$Head)
    if (-not $Database -or -not $Head) { return 'unknown' }
    if ($Database -notmatch '^\d+$' -or $Head -notmatch '^\d+$') { return 'unknown' }
    $a = [int]$Database; $b = [int]$Head
    if ($a -eq $b) { return 'current' }
    if ($a -lt $b) { return 'behind' }
    return 'ahead'
}

function Get-MigrationHead {
    param([Parameter(Mandatory)][string]$ApiDirectory, [hashtable]$Environment = @{})
    $r = Invoke-NativeCommand -FilePath 'uv' -Arguments @('run', '--no-sync', 'python', '-m', 'alembic', 'heads') -TimeoutSeconds 120 -Environment $Environment -WorkingDirectory $ApiDirectory
    if ($r.ExitCode -ne 0) { return $null }
    $match = [regex]::Match($r.StdOut, '(\d{4})\s+\(head\)')
    if ($match.Success) { return $match.Groups[1].Value }
    return $null
}

function Get-DatabaseRevision {
    param([Parameter(Mandatory)]$Settings, [Parameter(Mandatory)][string]$Database, [hashtable]$Ops)
    $Ops = Complete-Ops $Ops
    try {
        $rows = @(& $Ops.Psql $Settings $Database 'select version_num from alembic_version')
        if ($rows.Count -gt 0) { return $rows[0].Trim() }
    }
    catch {
        # A reachable but empty database has no version table yet: that is revision 0000 (nothing applied), not an error.
        if ($_.Exception.Message -match 'relation "alembic_version" does not exist') { return '0000' }
        Write-Verbose $_.Exception.Message
    }
    return $null
}

function Get-DetectorStatus {
    # Interprets the body of GET /health/models: 'ready' | 'warming-up' | 'worker-not-running' | 'probe-failed' | 'unavailable' | 'no-models'.
    param($ModelHealth)
    if ($null -eq $ModelHealth) { return [pscustomobject]@{ State = 'api-unreachable'; Detail = 'The API did not answer.' } }
    if (-not $ModelHealth.worker.running) { return [pscustomobject]@{ State = 'worker-not-running'; Detail = 'No worker is running, so analysis jobs wait in the queue.' } }
    $probe = $null
    if ($ModelHealth.worker.capabilities) { $probe = $ModelHealth.worker.capabilities.probe }
    if ($probe -eq 'running') { return [pscustomobject]@{ State = 'warming-up'; Detail = 'The worker is checking GPU and PyTorch (about 2-3 minutes after a cold start).' } }
    if ($probe -eq 'failed') { return [pscustomobject]@{ State = 'probe-failed'; Detail = 'The worker could not check GPU and PyTorch. Restart only the worker (scripts\stop.ps1 -Only worker, then scripts\dev.ps1).' } }
    $models = @($ModelHealth.models)
    if ($models.Count -eq 0) { return [pscustomobject]@{ State = 'no-models'; Detail = 'No model is listed in the manifest.' } }
    $missing = @($models | Where-Object { -not $_.available })
    if ($missing.Count -eq 0) { return [pscustomobject]@{ State = 'ready'; Detail = (($models | ForEach-Object { $_.name }) -join ', ') } }
    $reasons = ($missing | ForEach-Object { "$($_.name): $($_.reason_unavailable)" }) -join '; '
    return [pscustomobject]@{ State = 'unavailable'; Detail = $reasons }
}

function Save-ServiceWindows {
    param([hashtable]$WindowIds)
    New-Item -ItemType Directory -Force -Path $script:RunDir | Out-Null
    $record = [ordered]@{ saved_utc = (Get-Date).ToUniversalTime().ToString('o'); repo = $script:RepoRoot; windows = $WindowIds }
    ($record | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $script:PidFile -Encoding UTF8
}

function Read-ServiceWindows {
    if (-not (Test-Path -LiteralPath $script:PidFile)) { return @{} }
    try {
        $record = Get-Content -LiteralPath $script:PidFile -Raw | ConvertFrom-Json
        $ids = @{}
        foreach ($property in $record.windows.PSObject.Properties) { $ids[$property.Name] = [int]$property.Value }
        return $ids
    }
    catch { return @{} }
}

function Start-ServiceWindow {
    # Opens one service in its own PowerShell window (as before), with the project's cache folders and --no-sync.
    param([Parameter(Mandatory)][ValidateSet('api', 'worker', 'web')][string]$Name)
    $envScript = Join-Path $script:RepoRoot 'scripts\env.ps1'
    $apiDir = Join-Path $script:RepoRoot 'services\api'
    $webDir = Join-Path $script:RepoRoot 'apps\web'
    $command = switch ($Name) {
        'api' { ". '$envScript'; Set-Location '$apiDir'; uv run --no-sync python -m geopoc" }
        'worker' { ". '$envScript'; Set-Location '$apiDir'; uv run --no-sync python -m geopoc.jobs.worker" }
        'web' { ". '$envScript'; Set-Location '$webDir'; pnpm run dev" }
    }
    $process = Start-Process powershell -ArgumentList '-NoExit', '-Command', $command -PassThru
    return $process.Id
}
