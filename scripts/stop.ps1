# Stops what scripts\dev.ps1 started: the API, the worker, the web dev server and their windows.
# The database container is never touched.
#
#   .\scripts\stop.ps1                 stop all three
#   .\scripts\stop.ps1 -Only worker    stop only one of: api, worker, web
#   .\scripts\stop.ps1 -DryRun         list what would be stopped, stop nothing
#
# dev.ps1 records the windows it opens; those are stopped by process id (after checking the process is still the
# expected one). Services started some other way are found by their command line, scoped to this repository.
param(
    [ValidateSet('all', 'api', 'worker', 'web')][string]$Only = 'all',
    [switch]$DryRun
)
. "$PSScriptRoot\lib\common.ps1"
. "$PSScriptRoot\lib\startup.ps1"

$expected = @{ api = 'python -m geopoc'; worker = 'python -m geopoc.jobs.worker'; web = 'pnpm run dev' }
$names = if ($Only -eq 'all') { @('api', 'worker', 'web') } else { @($Only) }
$targets = New-Object System.Collections.Generic.List[object]
$all = @(Get-CimInstance Win32_Process)

# 1. Windows recorded by dev.ps1.
$windows = Read-ServiceWindows
foreach ($name in $names) {
    if (-not $windows.ContainsKey($name)) { continue }
    $process = $all | Where-Object { $_.ProcessId -eq $windows[$name] }
    if ($process -and $process.CommandLine -like "*$($expected[$name])*" -and $process.CommandLine -like "*$($script:RepoRoot)*") {
        $targets.Add([pscustomobject]@{ Name = $name; Id = $process.ProcessId; Why = 'window recorded by dev.ps1' })
    }
}

# 2. Anything else that matches, scoped to this repository (covers services started by an older dev.ps1).
$known = @($targets | ForEach-Object { $_.Id })
$descendants = {
    param($rootId)
    $found = New-Object System.Collections.Generic.List[int]
    $queue = New-Object System.Collections.Generic.Queue[int]
    $queue.Enqueue($rootId)
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        foreach ($child in ($all | Where-Object { $_.ParentProcessId -eq $current })) { $found.Add($child.ProcessId); $queue.Enqueue($child.ProcessId) }
    }
    return $found
}
$covered = New-Object System.Collections.Generic.HashSet[int]
foreach ($id in $known) { [void]$covered.Add($id); foreach ($d in (& $descendants $id)) { [void]$covered.Add($d) } }
# Never match this script itself or the shells that launched it (their command lines may quote the patterns above).
$self = New-Object System.Collections.Generic.HashSet[int]
$walker = [int]$PID
while ($walker -gt 0 -and $self.Add($walker)) {
    $parent = $all | Where-Object { $_.ProcessId -eq $walker } | Select-Object -First 1
    $walker = if ($parent) { [int]$parent.ParentProcessId } else { 0 }
}
foreach ($process in $all) {
    if ($covered.Contains([int]$process.ProcessId) -or $self.Contains([int]$process.ProcessId)) { continue }
    $command = $process.CommandLine
    if (-not $command) { continue }
    $match = $null
    if ($process.Name -eq 'python.exe') {
        if ($command -match '-m geopoc\.jobs\.(worker|run_job)') { $match = 'worker' }
        elseif ($command -match '-m geopoc(\s|$)') { $match = 'api' }
    }
    elseif ($process.Name -eq 'node.exe' -and $command -match 'vite' -and $command -like "*$($script:RepoRoot)*") { $match = 'web' }
    elseif ($process.Name -eq 'powershell.exe' -and $command -like "*$($script:RepoRoot)*") {
        if ($command -match 'python -m geopoc\.jobs\.worker') { $match = 'worker' }
        elseif ($command -match 'python -m geopoc(\s|$|"|'')') { $match = 'api' }
        elseif ($command -match 'pnpm run dev') { $match = 'web' }
    }
    if ($match -and $names -contains $match) { $targets.Add([pscustomobject]@{ Name = $match; Id = $process.ProcessId; Why = "$($process.Name) matched by command line" }) }
}

if ($targets.Count -eq 0) { Write-Host "Nothing to stop ($Only)."; if (-not $DryRun) { Remove-Item -LiteralPath $script:PidFile -Force -ErrorAction SilentlyContinue }; exit 0 }
foreach ($target in $targets) { Write-Host ("{0,-7} pid {1,-7} {2}" -f $target.Name, $target.Id, $target.Why) }
if ($DryRun) { Write-Host "Dry run: nothing was stopped."; exit 0 }

foreach ($target in $targets) {
    $result = Invoke-NativeCommand -FilePath 'taskkill' -Arguments @('/PID', [string]$target.Id, '/T', '/F') -TimeoutSeconds 30
    if ($result.ExitCode -ne 0 -and $result.StdErr -notmatch 'not found') { Write-Host "Could not stop pid $($target.Id): $($result.StdErr.Trim())" -ForegroundColor Yellow }
}
if ($Only -eq 'all') { Remove-Item -LiteralPath $script:PidFile -Force -ErrorAction SilentlyContinue }
else {
    $remaining = Read-ServiceWindows
    $remaining.Remove($Only)
    if ($remaining.Count -gt 0) { Save-ServiceWindows -WindowIds $remaining } else { Remove-Item -LiteralPath $script:PidFile -Force -ErrorAction SilentlyContinue }
}
Write-Host "Stopped $($targets.Count) process(es). The database container is still running."
