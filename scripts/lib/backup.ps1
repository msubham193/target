# Database backup and backup verification. Dot-source after common.ps1.
#
# A backup is two files side by side:  <name>.dump  and  <name>.dump.manifest.json (plus <name>.dump.sha256).
# A backup counts as "verified" only when the dump was restored into a disposable database created by this script
# and the table contents matched the source. Other scripts (the reset and migrate scripts) accept only such backups.

$script:DefaultBackupRoot = 'D:\geopoc-backups\db'
$script:VerifyNamePattern = '^geopoc_verify_[0-9]{14}$'
$script:HeartbeatTable = 'worker_heartbeat'   # rewritten every few seconds by a live worker, so it is never compared

function Find-PostgresTool {
    # pg_dump / pg_restore: an explicit folder, then PATH, then the usual install folders (newest first).
    param([Parameter(Mandatory)][ValidateSet('pg_dump', 'pg_restore')][string]$Name)
    $exe = "$Name.exe"
    $candidates = @()
    if ($env:GEOPOC_PG_BIN) { $candidates += (Join-Path $env:GEOPOC_PG_BIN $exe) }
    $onPath = Get-Command $exe -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    foreach ($base in @('C:\Program Files\PostgreSQL', 'C:\Program Files (x86)\PostgreSQL')) {
        if (Test-Path -LiteralPath $base) {
            $candidates += @(Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue | Sort-Object { [int]($_.Name -replace '\D', '') } -Descending | ForEach-Object { Join-Path $_.FullName "bin\$exe" })
        }
    }
    foreach ($pgAdmin in @('C:\Program Files\pgAdmin 4', (Join-Path $env:LOCALAPPDATA 'Programs\pgAdmin 4'))) {
        $candidates += (Join-Path $pgAdmin "runtime\$exe")
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Get-TableRowCounts {
    # Exact row counts of every table in the public schema: @{ table = count }.
    param([Parameter(Mandatory)]$Settings, [Parameter(Mandatory)][string]$Database, [Parameter(Mandatory)][hashtable]$Ops)
    $tables = @(& $Ops.Psql $Settings $Database "select table_name from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by 1")
    $counts = @{}
    if ($tables.Count -eq 0) { return $counts }
    $union = ($tables | ForEach-Object { "select '$_' as t, count(*) as n from public.""$_""" }) -join ' union all '
    foreach ($line in @(& $Ops.Psql $Settings $Database $union)) {
        $parts = $line -split '\|'
        if ($parts.Count -eq 2) { $counts[$parts[0]] = [int64]$parts[1] }
    }
    return $counts
}

function Compare-TableRowCounts {
    param([hashtable]$Source, [hashtable]$Copy)
    $differences = @()
    foreach ($table in (@($Source.Keys) + @($Copy.Keys) | Sort-Object -Unique)) {
        if ($table -eq $script:HeartbeatTable) { continue }
        $a = if ($Source.ContainsKey($table)) { $Source[$table] } else { 'missing' }
        $b = if ($Copy.ContainsKey($table)) { $Copy[$table] } else { 'missing' }
        if ("$a" -ne "$b") { $differences += "${table}: source $a, restored copy $b" }
    }
    return $differences
}

function Get-Sha256 {
    param([Parameter(Mandatory)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function New-UniqueBackupPath {
    param([string]$Directory, [string]$Database, [datetime]$When)
    $stamp = $When.ToString('yyyyMMdd-HHmmss')
    for ($i = 0; $i -lt 50; $i++) {
        $suffix = ([guid]::NewGuid().ToString('N')).Substring(0, 4)
        $path = Join-Path $Directory ("{0}-{1}-{2}.dump" -f $Database, $stamp, $suffix)
        if (-not (Test-Path -LiteralPath $path) -and -not (Test-Path -LiteralPath "$path.part")) { return $path }
    }
    throw 'Could not find an unused backup file name.'
}

function Invoke-DatabaseBackup {
    <#
      Dumps one database to a new file (never overwrites), records its SHA-256, checks that the archive can be read,
      and optionally verifies it by restoring into a disposable database and comparing table contents.
      Returns an object; it throws only for programming errors, never for an ordinary failed backup.
    #>
    param(
        [Parameter(Mandatory)]$Settings,
        [string]$Database,
        [string]$OutputDir = $script:DefaultBackupRoot,
        [switch]$Verify,
        [switch]$KeepVerifyDatabase,
        [hashtable]$Ops,
        [long]$MinFreeBytes = 1GB
    )
    $Ops = Complete-Ops $Ops
    $result = [pscustomobject]@{ Success = $false; Database = $null; File = $null; Bytes = 0; Sha256 = $null; ManifestPath = $null; Verified = $false; Messages = @(); Error = $null }
    $fail = { param($message) $result.Error = $message; return $result }

    if (-not $Database) { $Database = $Settings.DbName }
    $result.Database = $Database
    if ($Database -notmatch '^[A-Za-z0-9_]+$') { return (& $fail "Refusing database name '$Database': only letters, digits and underscores are allowed.") }

    $dump = & $Ops.FindTool 'pg_dump'
    $restore = & $Ops.FindTool 'pg_restore'
    if (-not $dump) { return (& $fail 'pg_dump was not found. Install PostgreSQL client tools or set GEOPOC_PG_BIN to their folder.') }
    if (-not $restore) { return (& $fail 'pg_restore was not found next to pg_dump. Set GEOPOC_PG_BIN to a folder containing both.') }

    try {
        New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
        $drive = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($OutputDir))
        $free = (New-Object System.IO.DriveInfo($drive)).AvailableFreeSpace
        if ($free -lt $MinFreeBytes) { return (& $fail "Only $(Format-Bytes $free) free on $drive; at least $(Format-Bytes $MinFreeBytes) is required.") }
    }
    catch { return (& $fail "The output folder '$OutputDir' cannot be used: $($_.Exception.Message)") }

    $started = & $Ops.Now
    $path = New-UniqueBackupPath -Directory $OutputDir -Database $Database -When $started
    $part = "$path.part"
    $environment = @{ PGPASSWORD = $Settings.GetDbPassword() }

    $dumpArguments = @('-h', $Settings.DbHost, '-p', [string]$Settings.DbPort, '-U', $Settings.DbUser, '-d', $Database, '-Fc', '-Z', '6', '--no-password', '-f', $part)
    $run = & $Ops.Run $dump $dumpArguments 600 $environment
    if ($run.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $part)) {
        Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
        $detail = Hide-Secret (($run.StdErr + '').Trim()) $Settings
        return (& $fail "pg_dump failed (exit $($run.ExitCode)); no backup file was kept. $detail")
    }
    if ((Get-Item -LiteralPath $part).Length -eq 0) {
        Remove-Item -LiteralPath $part -Force
        return (& $fail 'pg_dump produced an empty file; it was removed.')
    }
    Move-Item -LiteralPath $part -Destination $path   # appears under its final name only when complete
    $result.File = $path
    $result.Bytes = (Get-Item -LiteralPath $path).Length
    $result.Sha256 = Get-Sha256 $path

    # The archive must be readable.
    $list = & $Ops.Run $restore @('--list', $path) 60 @{}
    $toc = @(($list.StdOut -split "`r?`n") | Where-Object { $_ -and -not $_.StartsWith(';') })
    if ($list.ExitCode -ne 0 -or $toc.Count -eq 0) {
        $result.Messages += "KEPT BUT SUSPECT: pg_restore could not read the archive ($(Hide-Secret (($list.StdErr + '').Trim()) $Settings))."
        return (& $fail 'The dump was written but its table of contents could not be read, so it is not trustworthy.')
    }
    $toolVersion = ((& $Ops.Run $dump @('--version') 20 @{}).StdOut + '').Trim()

    $serverVersion = $null
    $revision = $null
    try {
        $info = @(& $Ops.Psql $Settings $Database "select current_setting('server_version'), coalesce((select version_num from alembic_version limit 1), '')")
        if ($info.Count -gt 0) { $parts = $info[0] -split '\|'; $serverVersion = $parts[0]; $revision = $parts[1] }
    }
    catch { $result.Messages += "Server version and migration revision could not be read: $($_.Exception.Message)" }

    $verification = $null
    if ($Verify) {
        $verification = [ordered]@{ method = 'restore into a disposable database and compare row counts'; database = $null; tables_compared = 0; differences = @(); temporary_database_dropped = $false; error = $null }
        $tempName = 'geopoc_verify_' + $started.ToString('yyyyMMddHHmmss')
        if ($tempName -notmatch $script:VerifyNamePattern) { return (& $fail "Internal error: bad verification database name '$tempName'.") }
        $verification.database = $tempName
        $created = $false
        try {
            $existing = @(& $Ops.Psql $Settings 'postgres' "select 1 from pg_database where datname = '$tempName'")
            if ($existing.Count -gt 0) { throw "A database named $tempName already exists; refusing to restore over it." }
            [void](& $Ops.Psql $Settings 'postgres' "create database ""$tempName""")
            $created = $true
            $restoreRun = & $Ops.Run $restore @('-h', $Settings.DbHost, '-p', [string]$Settings.DbPort, '-U', $Settings.DbUser, '-d', $tempName, '--no-password', '--exit-on-error', $path) 600 $environment
            if ($restoreRun.ExitCode -ne 0) { throw "pg_restore failed (exit $($restoreRun.ExitCode)): $(Hide-Secret (($restoreRun.StdErr + '').Trim()) $Settings)" }
            $source = Get-TableRowCounts -Settings $Settings -Database $Database -Ops $Ops
            $copy = Get-TableRowCounts -Settings $Settings -Database $tempName -Ops $Ops
            $verification.tables_compared = ($source.Keys | Where-Object { $_ -ne $script:HeartbeatTable }).Count
            $verification.differences = @(Compare-TableRowCounts -Source $source -Copy $copy)
            $result.Verified = ($verification.differences.Count -eq 0 -and $verification.tables_compared -gt 0)
            if (-not $result.Verified) { $result.Messages += 'Verification found differences between the source database and the restored copy (see the manifest).' }
        }
        catch {
            $verification.error = $_.Exception.Message
            $result.Messages += "Verification failed: $($_.Exception.Message)"
        }
        finally {
            if ($created -and -not $KeepVerifyDatabase -and $tempName -match $script:VerifyNamePattern) {
                try { [void](& $Ops.Psql $Settings 'postgres' "drop database if exists ""$tempName"" with (force)"); $verification.temporary_database_dropped = $true }
                catch { $result.Messages += "The temporary database $tempName could not be dropped: $($_.Exception.Message)" }
            }
        }
    }

    $manifest = [ordered]@{
        schema = 1
        tool = 'scripts/backup-db.ps1'
        database = $Database
        created_utc = $started.ToUniversalTime().ToString('o')
        file = (Split-Path -Leaf $path)
        bytes = $result.Bytes
        sha256 = $result.Sha256
        pg_dump_version = $toolVersion
        server_version = $serverVersion
        alembic_revision = $revision
        toc_entries = $toc.Count
        verified = $result.Verified
        verification = $verification
    }
    $manifestPath = "$path.manifest.json"
    ($manifest | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    ("{0}  {1}" -f $result.Sha256, (Split-Path -Leaf $path)) | Set-Content -LiteralPath "$path.sha256" -Encoding ASCII
    $result.ManifestPath = $manifestPath
    $result.Success = $true
    return $result
}

function Get-LatestBackupManifest {
    param([Parameter(Mandatory)][string]$Database, [string]$BackupRoot = $script:DefaultBackupRoot)
    if (-not (Test-Path -LiteralPath $BackupRoot)) { return $null }
    $best = $null
    foreach ($file in @(Get-ChildItem -LiteralPath $BackupRoot -Filter '*.manifest.json' -File -ErrorAction SilentlyContinue)) {
        try { $manifest = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json } catch { continue }
        if ($manifest.database -ne $Database) { continue }
        $created = [datetime]::Parse($manifest.created_utc, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
        if ($null -eq $best -or $created -gt $best.Created) { $best = [pscustomobject]@{ Path = $file.FullName; Created = $created } }
    }
    if ($best) { return $best.Path }
    return $null
}

function Test-RecentVerifiedBackup {
    # True only for a manifest of this database that is recent, marked verified, and whose dump still has its recorded hash.
    param(
        [Parameter(Mandatory)][string]$Database,
        [string]$ManifestPath,
        [string]$BackupRoot = $script:DefaultBackupRoot,
        [int]$MaxAgeHours = 24,
        [hashtable]$Ops
    )
    $Ops = Complete-Ops $Ops
    $refuse = { param($reason) return [pscustomobject]@{ Ok = $false; Reason = $reason; ManifestPath = $ManifestPath; AgeHours = $null } }
    if (-not $ManifestPath) { $ManifestPath = Get-LatestBackupManifest -Database $Database -BackupRoot $BackupRoot }
    if (-not $ManifestPath) { return (& $refuse "No backup manifest for database '$Database' was found under $BackupRoot. Run scripts\backup-db.ps1 -Verify first.") }
    if (-not (Test-Path -LiteralPath $ManifestPath)) { return (& $refuse "Backup manifest not found: $ManifestPath") }
    try { $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json }
    catch { return (& $refuse "Backup manifest cannot be read: $ManifestPath") }
    if ($manifest.schema -ne 1) { return (& $refuse 'Backup manifest has an unknown schema version.') }
    if ($manifest.database -ne $Database) { return (& $refuse "The backup is of database '$($manifest.database)', not '$Database'.") }
    if ($manifest.verified -ne $true) { return (& $refuse 'The backup was not verified by a test restore. Run scripts\backup-db.ps1 -Verify.') }
    $created = [datetime]::Parse($manifest.created_utc, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
    $age = ((& $Ops.Now).ToUniversalTime() - $created).TotalHours
    if ($age -gt $MaxAgeHours) { return (& $refuse ("The newest verified backup is {0:N1} hours old; the limit is {1} hours." -f $age, $MaxAgeHours)) }
    if ($age -lt -0.1) { return (& $refuse 'The backup manifest is dated in the future.') }
    $dumpPath = Join-Path (Split-Path -Parent $ManifestPath) $manifest.file
    if (-not (Test-Path -LiteralPath $dumpPath)) { return (& $refuse "The backup file named in the manifest is missing: $dumpPath") }
    if ((Get-Item -LiteralPath $dumpPath).Length -ne [int64]$manifest.bytes) { return (& $refuse 'The backup file size differs from its manifest.') }
    if ((Get-Sha256 $dumpPath) -ne $manifest.sha256) { return (& $refuse 'The backup file does not match the SHA-256 in its manifest.') }
    return [pscustomobject]@{ Ok = $true; Reason = 'ok'; ManifestPath = $ManifestPath; AgeHours = $age }
}
