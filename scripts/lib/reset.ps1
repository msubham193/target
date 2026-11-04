# Guarded reset of application data. Dot-source after common.ps1 and backup.ps1.
#
# What a reset does, and only when explicitly executed:
#   1. moves <data root>\originals, previews and tmp into a timestamped quarantine folder (a rename: nothing is deleted);
#   2. empties every application table in the target database (TRUNCATE ... CASCADE), except alembic_version and PostGIS tables.
# The default is a dry run that changes nothing.

$script:DataFolders = @('originals', 'previews', 'tmp')
$script:KeepTables = @('alembic_version', 'spatial_ref_sys')
$script:DefaultQuarantineRoot = 'D:\geopoc-quarantine'

function Get-ResetPhrase {
    param([bool]$Working, [string]$Database, [string]$DataRootDisplay)
    if ($Working) { return "ERASE ALL DATA IN DATABASE $Database AND QUARANTINE FILES UNDER $DataRootDisplay" }
    return "ERASE SCRATCH DATABASE $Database"
}

function Get-DataFolderSummary {
    param([Parameter(Mandatory)][string]$DataRoot)
    foreach ($name in $script:DataFolders) {
        $path = Join-Path $DataRoot $name
        if (Test-Path -LiteralPath $path) {
            $files = @(Get-ChildItem -LiteralPath $path -Recurse -Force -File -ErrorAction SilentlyContinue)
            [pscustomobject]@{ Name = $name; Path = $path; Exists = $true; Files = $files.Count; Bytes = [int64](($files | Measure-Object Length -Sum).Sum) }
        }
        else { [pscustomobject]@{ Name = $name; Path = $path; Exists = $false; Files = 0; Bytes = 0 } }
    }
}

function Get-AppTables {
    param([Parameter(Mandatory)]$Settings, [Parameter(Mandatory)][string]$Database, [Parameter(Mandatory)][hashtable]$Ops)
    $keep = ($script:KeepTables | ForEach-Object { "'$_'" }) -join ', '
    return @(& $Ops.Psql $Settings $Database "select table_name from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' and table_name not in ($keep) order by 1")
}

function Clear-AppTables {
    param([Parameter(Mandatory)]$Settings, [Parameter(Mandatory)][string]$Database, [Parameter(Mandatory)][hashtable]$Ops)
    $tables = Get-AppTables -Settings $Settings -Database $Database -Ops $Ops
    if ($tables.Count -eq 0) { throw "No application tables were found in '$Database'; nothing was emptied." }
    $list = ($tables | ForEach-Object { "public.""$_""" }) -join ', '
    [void](& $Ops.Psql $Settings $Database "truncate table $list restart identity cascade")
    return $tables.Count
}

function Get-ResetGuardResults {
    # Every condition that must hold before data is erased, as a list of { Name; Passed; Detail }.
    param(
        [Parameter(Mandatory)]$Settings,
        [Parameter(Mandatory)][string]$Target,
        [Parameter(Mandatory)][bool]$Working,
        [string]$DataRoot,
        [bool]$Authorized,
        [bool]$Force,
        [string]$Confirmation,
        [string]$BackupManifest,
        [string]$BackupRoot,
        [int]$MaxBackupAgeHours,
        [string]$QuarantineRoot,
        [hashtable]$Ops
    )
    $results = New-Object System.Collections.Generic.List[object]
    $add = { param($name, $passed, $detail) $results.Add([pscustomobject]@{ Name = $name; Passed = [bool]$passed; Detail = $detail }) }

    $display = if ($Working) { $Settings.DataRoot } else { $DataRoot }
    $phrase = Get-ResetPhrase -Working $Working -Database $Target -DataRootDisplay $display

    if ($Working) {
        & $add 'working-data authorization' $Authorized 'pass -AuthorizeWorkingData to authorize erasing the WORKING database'
        $matchesConfig = ($Target -ceq $Settings.DbName)
        $configDetail = "target '$Target' is the configured database"
        if (-not $matchesConfig) { $configDetail = "target '$Target' does not match the configured database '$($Settings.DbName)'" }
        & $add 'database matches the application configuration' $matchesConfig $configDetail
        $phraseOk = ($Confirmation -ceq $phrase)
        & $add 'full confirmation phrase' $phraseOk "type exactly: $phrase   (-Force does not skip this for the working database)"
        $services = & $Ops.Services $Settings
        $running = @($services.Running)
        $stoppedDetail = 'nothing is running'
        if ($running.Count -gt 0) { $stoppedDetail = 'still running: ' + ($running -join ', ') + ' (run scripts\stop.ps1)' }
        & $add 'API, worker and web app are stopped' ($running.Count -eq 0) $stoppedDetail
        $backup = Test-RecentVerifiedBackup -Database $Target -ManifestPath $BackupManifest -BackupRoot $BackupRoot -MaxAgeHours $MaxBackupAgeHours -Ops $Ops
        $backupDetail = $backup.Reason
        if ($backup.Ok) { $backupDetail = ('{0} ({1:N1} h old)' -f (Split-Path -Leaf $backup.ManifestPath), $backup.AgeHours) }
        & $add 'recent verified backup' $backup.Ok $backupDetail
    }
    else {
        $phraseOk = ($Force -or ($Confirmation -ceq $phrase))
        & $add 'confirmation (or -Force, scratch databases only)' $phraseOk "type exactly: $phrase"
        $leaf = if ($DataRoot) { Split-Path -Leaf $DataRoot } else { '' }
        $configured = $Settings.DataRootPath.TrimEnd('\')
        $given = if ($DataRoot) { [IO.Path]::GetFullPath($DataRoot).TrimEnd('\') } else { '' }
        # The only supported layout is a direct child of the configured data root, named _<name> (D:\geopoc-data\_e2e, _smoke).
        # A bare underscore, dots, spaces or an unrelated folder elsewhere that merely starts with '_' are refused.
        $parent = if ($given) { [IO.Path]::GetDirectoryName($given) } else { $null }
        $valid = ($DataRoot -and ($leaf -cmatch '^_[A-Za-z0-9][A-Za-z0-9_-]*$') -and $parent -and ($parent.TrimEnd('\') -ieq $configured) -and ($given -ine $configured))
        & $add 'scratch data folder is explicit and separate' $valid 'for a scratch database give -DataRoot as a direct child of the working data root named _<name> (for example D:\geopoc-data\_e2e), never the working data root itself'
    }

    $sameVolume = $true
    if ($QuarantineRoot -and $DataRoot) {
        $sameVolume = ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($QuarantineRoot)) -ieq [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($DataRoot)))
    }
    & $add 'quarantine folder is on the same drive (a move, not a copy)' $sameVolume "quarantine $QuarantineRoot, data $DataRoot"
    return $results.ToArray()   # not @($results): Windows PowerShell 5.1 fails binding a generic list of objects there
}

function Invoke-DataReset {
    param(
        [Parameter(Mandatory)]$Settings,
        [string]$Database,
        [string]$DataRoot,
        [switch]$Execute,
        [switch]$AuthorizeWorkingData,
        [switch]$Force,
        [string]$Confirmation,
        [string]$BackupManifest,
        [string]$BackupRoot = $script:DefaultBackupRoot,
        [string]$QuarantineRoot = $script:DefaultQuarantineRoot,
        [int]$MaxBackupAgeHours = 24,
        [hashtable]$Ops
    )
    $Ops = Complete-Ops $Ops
    $report = New-Object System.Collections.Generic.List[string]
    $outcome = [pscustomobject]@{ Outcome = 'refused'; Working = $null; Target = $null; Report = $report; Failures = @(); Warnings = @(); Phrase = $null; Quarantine = $null; DataRoot = $null }
    $say = { param($line) $report.Add($line) }

    $target = if ($Database) { $Database } else { $Settings.DbName }
    $outcome.Target = $target
    if ($target -notmatch '^[A-Za-z0-9_]+$') {
        $outcome.Failures = @("Refusing database name '$target': only letters, digits and underscores are allowed.")
        return $outcome
    }
    $working = -not (Test-ScratchDatabaseName $target)
    $outcome.Working = $working

    if ($working) {
        if ($DataRoot -and ([IO.Path]::GetFullPath($DataRoot).TrimEnd('\') -ine $Settings.DataRootPath.TrimEnd('\'))) {
            $outcome.Failures = @("-DataRoot '$DataRoot' is not the configured data root ($($Settings.DataRoot)). For the working database the data root always comes from the configuration.")
            return $outcome
        }
        $root = $Settings.DataRootPath
    }
    else { $root = if ($DataRoot) { [IO.Path]::GetFullPath($DataRoot) } else { '' } }
    $outcome.DataRoot = $root
    if ($working -and $Force) { $outcome.Warnings += '-Force is ignored for the working database.' }

    # --- What would be affected (read-only) ---
    & $say "Target database : $target ($(if ($working) { 'WORKING database' } else { 'scratch database' }))"
    & $say "Server          : $($Settings.DbHost):$($Settings.DbPort) (container $script:PostgresContainer)"
    & $say "Configured name : $($Settings.DbName)"
    $counts = $null
    try {
        $tables = Get-AppTables -Settings $Settings -Database $target -Ops $Ops
        $union = ($tables | ForEach-Object { "select '$_' as t, count(*) as n from public.""$_""" }) -join ' union all '
        $counts = @{}
        if ($tables.Count -gt 0) { foreach ($line in @(& $Ops.Psql $Settings $target $union)) { $p = $line -split '\|'; if ($p.Count -eq 2) { $counts[$p[0]] = [int64]$p[1] } } }
        $total = ($counts.Values | Measure-Object -Sum).Sum
        & $say "Tables that would be emptied: $($tables.Count), holding $total rows in total:"
        foreach ($name in ($counts.Keys | Sort-Object)) { & $say ("    {0,-24} {1,8}" -f $name, $counts[$name]) }
        & $say "Kept: $($script:KeepTables -join ', ') (schema revision and PostGIS reference data)"
    }
    catch {
        & $say "The database could not be read: $($_.Exception.Message)"
        $outcome.Failures += "The target database could not be inspected, so nothing will be done: $($_.Exception.Message)"
    }
    if ($root) {
        & $say "Data root       : $root"
        $folders = @(Get-DataFolderSummary -DataRoot $root)
        foreach ($f in $folders) {
            if ($f.Exists) { & $say ("    {0,-10} {1,6} files  {2,12}   -> would be MOVED to quarantine" -f $f.Name, $f.Files, (Format-Bytes $f.Bytes)) }
            else { & $say ("    {0,-10} (not present)" -f $f.Name) }
        }
        & $say "Never touched: $($script:DataFolders.Count) listed folders are the only ones moved; weights and every other folder stay."
    }
    else { & $say 'Data root       : none given (a scratch reset needs -DataRoot).' }

    $stamp = (& $Ops.Now).ToString('yyyyMMdd-HHmmss')
    $quarantine = Join-Path $QuarantineRoot ("{0}-{1}" -f $target, $stamp)
    $outcome.Quarantine = $quarantine
    $outcome.Phrase = Get-ResetPhrase -Working $working -Database $target -DataRootDisplay $(if ($working) { $Settings.DataRoot } else { $root })

    $guards = Get-ResetGuardResults -Settings $Settings -Target $target -Working $working -DataRoot $root -Authorized ([bool]$AuthorizeWorkingData) -Force ([bool]$Force) -Confirmation $Confirmation -BackupManifest $BackupManifest -BackupRoot $BackupRoot -MaxBackupAgeHours $MaxBackupAgeHours -QuarantineRoot $QuarantineRoot -Ops $Ops
    & $say ''
    & $say 'Conditions required before anything is erased:'
    foreach ($g in $guards) { & $say ("    [{0}] {1}  - {2}" -f $(if ($g.Passed) { 'ok' } else { 'NO' }), $g.Name, $g.Detail) }
    $guardFailures = @($guards | Where-Object { -not $_.Passed } | ForEach-Object { $_.Name + ': ' + $_.Detail })

    if (-not $Execute) {
        $outcome.Outcome = 'dry-run'
        $outcome.Failures = @($outcome.Failures) + @()
        & $say ''
        & $say 'DRY RUN: nothing was changed. Add -Execute to erase (all conditions above must then be satisfied).'
        return $outcome
    }

    $allFailures = @($outcome.Failures) + $guardFailures
    if ($allFailures.Count -gt 0) {
        $outcome.Failures = $allFailures
        $outcome.Outcome = 'refused'
        & $say ''
        & $say 'REFUSED: nothing was changed.'
        return $outcome
    }

    # --- Authorized: files first (a reversible rename), then the database; undo the files if the database step fails ---
    $moved = New-Object System.Collections.Generic.List[object]
    try {
        New-Item -ItemType Directory -Path $quarantine -ErrorAction Stop | Out-Null
        foreach ($f in $folders) {
            if (-not $f.Exists) { continue }
            $destination = Join-Path $quarantine $f.Name
            & $Ops.Move $f.Path $destination
            $moved.Add([pscustomobject]@{ From = $f.Path; To = $destination; Files = $f.Files; Bytes = $f.Bytes })
        }
        $emptied = Clear-AppTables -Settings $Settings -Database $target -Ops $Ops
    }
    catch {
        $problem = $_.Exception.Message
        foreach ($item in $moved) {
            try { & $Ops.Move $item.To $item.From } catch { $problem += " | COULD NOT RESTORE $($item.From) from $($item.To)" }
        }
        $outcome.Outcome = 'failed'
        $outcome.Failures = @("The reset did not complete: $problem")
        & $say 'FAILED: moved folders were put back where possible; see the failure text.'
        return $outcome
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $root 'originals'), (Join-Path $root 'tmp\uploads') | Out-Null
    $record = [ordered]@{
        performed_utc = (& $Ops.Now).ToUniversalTime().ToString('o')
        database = $target
        working = $working
        tables_emptied = $emptied
        row_counts_before = $counts
        folders_moved = $moved.ToArray()
        note = 'Originals and previews were moved here, not deleted. Move them back only together with a matching database restore.'
    }
    ($record | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath (Join-Path $quarantine 'RESET-RECORD.json') -Encoding UTF8
    $outcome.Outcome = 'done'
    & $say ''
    & $say "DONE: $emptied tables emptied in '$target'. Files were moved (not deleted) to: $quarantine"
    return $outcome
}
