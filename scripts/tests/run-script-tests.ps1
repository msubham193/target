# Tests for the project scripts. Safe by construction:
#   - every fixture lives in a sandbox folder under D:\geopoc-cache\script-tests\ and is removed afterwards;
#   - the settings objects are synthetic and point at the sandbox, never at the real .env or the real data root;
#   - the optional integration part uses only the scratch database geopoc_script_test, which it creates and drops itself.
# Nothing here reads or writes the working database, and no application service is started or stopped.
#
#   .\scripts\tests\run-script-tests.ps1                    all tests
#   .\scripts\tests\run-script-tests.ps1 -SkipIntegration   no Docker, no database
param([switch]$SkipIntegration)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\env.ps1"
. "$PSScriptRoot\..\lib\common.ps1"
. "$PSScriptRoot\..\lib\backup.ps1"
. "$PSScriptRoot\..\lib\reset.ps1"
. "$PSScriptRoot\..\lib\startup.ps1"
. "$PSScriptRoot\..\lib\smoke.ps1"

$script:Failures = New-Object System.Collections.Generic.List[string]
$script:Passed = 0
$script:Skipped = 0
$sandboxBase = 'D:\geopoc-cache\script-tests'
$sandbox = Join-Path $sandboxBase ('run-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $sandbox | Out-Null
$repo = $script:RepoRoot
$FakePassword = 'fake-secret-pw-123'

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passed++; Write-Host "PASS $Name" }
    catch { $script:Failures.Add("${Name}: $($_.Exception.Message)"); Write-Host "FAIL $Name -- $($_.Exception.Message)" -ForegroundColor Red }
}
function Skip-Case { param([string]$Name, [string]$Reason) $script:Skipped++; Write-Host "SKIP $Name -- $Reason" -ForegroundColor Yellow }
function Assert-True { param($Condition, [string]$Message = 'expected true') if (-not $Condition) { throw $Message } }
function Assert-Equal { param($Actual, $Expected, [string]$Message = '') if ("$Actual" -cne "$Expected") { throw "expected '$Expected' but got '$Actual'. $Message" } }
function Assert-Match { param([string]$Text, [string]$Pattern, [string]$Message = '') if ($Text -notmatch $Pattern) { throw "expected text to match /$Pattern/ but it was: $Text. $Message" } }
function Assert-NotMatch { param([string]$Text, [string]$Pattern, [string]$Message = '') if ($Text -match $Pattern) { throw "text must not match /$Pattern/ but it was: $Text. $Message" } }

function New-FakeSettings {
    param([string]$DbName = 'geopoc', [Parameter(Mandatory)][string]$DataRoot)
    $settings = [pscustomobject]@{
        EnvFile = 'synthetic'; EnvFileFound = $true; DbHost = '127.0.0.1'; DbPort = 5433; DbUser = 'geopoc'; DbName = $DbName
        HasDbPassword = $true; DataRoot = ($DataRoot -replace '\\', '/'); DataRootPath = [IO.Path]::GetFullPath($DataRoot)
        ApiHost = '127.0.0.1'; ApiPort = 8000; WebPort = 5173
    }
    $settings | Add-Member -MemberType ScriptMethod -Name GetDbPassword -Value ({ 'fake-secret-pw-123' }.GetNewClosure())
    return $settings
}

function New-DataRoot {
    param([Parameter(Mandatory)][string]$Path)
    foreach ($folder in 'originals\aa\bb', 'previews', 'tmp\uploads', 'weights\model') { New-Item -ItemType Directory -Force -Path (Join-Path $Path $folder) | Out-Null }
    foreach ($i in 1..3) { Set-Content -LiteralPath (Join-Path $Path "originals\aa\bb\file$i") -Value ('original ' * 50 + $i) }
    foreach ($i in 1..2) { Set-Content -LiteralPath (Join-Path $Path "previews\p$i.png") -Value 'preview' }
    Set-Content -LiteralPath (Join-Path $Path 'weights\model\w.pth') -Value 'weights'
    return $Path
}

function New-ResetOps {
    param([bool]$ServicesRunning = $false, [bool]$TruncateFails = $false, [hashtable]$Extra = @{})
    $log = New-Object System.Collections.Generic.List[string]
    $ops = @{
        Psql = {
            param($Settings, $Database, $Sql)
            $log.Add("$Database :: $Sql")
            if ($Sql -match 'information_schema') { return @('asset', 'audit_event', 'project') }
            if ($Sql -match 'count\(\*\)') { return @('asset|3', 'audit_event|5', 'project|1') }
            if ($Sql -match '^truncate') { if ($TruncateFails) { throw 'simulated truncate failure' }; return @() }
            return @()
        }.GetNewClosure()
        Services = { param($Settings) [pscustomobject]@{ Running = @(if ($ServicesRunning) { 'API (port 8000)'; 'worker' }) } }.GetNewClosure()
        Now = { [datetime]::new(2026, 10, 29, 12, 0, 0, [DateTimeKind]::Utc) }
    }
    foreach ($key in $Extra.Keys) { $ops[$key] = $Extra[$key] }
    return [pscustomobject]@{ Ops = $ops; Log = $log }
}

function New-FakeBackup {
    # A backup folder holding one dump and its manifest, as scripts\backup-db.ps1 would leave it.
    param([Parameter(Mandatory)][string]$Dir, [string]$Database = 'geopoc', [double]$AgeHours = 1, [bool]$Verified = $true, [switch]$TamperDump, [switch]$MissingDump)
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $now = [datetime]::new(2026, 10, 29, 12, 0, 0, [DateTimeKind]::Utc)
    $name = "$Database-fake-$([guid]::NewGuid().ToString('N').Substring(0, 4)).dump"
    $dump = Join-Path $Dir $name
    [IO.File]::WriteAllBytes($dump, [byte[]](1..255))
    $hash = (Get-FileHash -LiteralPath $dump -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = (Get-Item -LiteralPath $dump).Length
    $manifest = [ordered]@{ schema = 1; database = $Database; created_utc = $now.AddHours(-$AgeHours).ToString('o'); file = $name; bytes = $size; sha256 = $hash; verified = $Verified }
    ($manifest | ConvertTo-Json) | Set-Content -LiteralPath "$dump.manifest.json" -Encoding UTF8
    if ($TamperDump) { [IO.File]::WriteAllBytes($dump, [byte[]](9..120)) }
    if ($MissingDump) { Remove-Item -LiteralPath $dump -Force }
    return "$dump.manifest.json"
}

# ============================================================================================================
Write-Host "`n--- settings, secrets and names ---"

Test-Case 'Read-DotEnv ignores comments and strips quotes' {
    $file = Join-Path $sandbox 'a.env'
    Set-Content -LiteralPath $file -Value @('# comment', '', 'GEOPOC_DB_NAME=geopoc', 'GEOPOC_DB_PASSWORD="quoted pw"', "GEOPOC_X='single'", 'not a pair')
    $v = Read-DotEnv -Path $file
    Assert-Equal $v['GEOPOC_DB_NAME'] 'geopoc'
    Assert-Equal $v['GEOPOC_DB_PASSWORD'] 'quoted pw'
    Assert-Equal $v['GEOPOC_X'] 'single'
    Assert-Equal $v.Count 3
}

Test-Case 'Get-GeopocSettings never shows the password when printed or formatted' {
    $file = Join-Path $sandbox 'b.env'
    Set-Content -LiteralPath $file -Value @(('GEOPOC_DB_PASSWORD=' + $FakePassword), 'GEOPOC_DB_NAME=geopoc', 'GEOPOC_DATA_ROOT=D:/some/root')
    $s = Get-GeopocSettings -EnvFile $file
    Assert-True $s.HasDbPassword
    Assert-Equal $s.GetDbPassword() $FakePassword
    $printed = ($s | Format-List * | Out-String) + ($s | ConvertTo-Json -Depth 3) + ($s | Out-String) + "$s"
    Assert-NotMatch $printed ([regex]::Escape($FakePassword)) 'the password leaked into formatted output'
    Assert-Equal $s.DataRoot 'D:/some/root'
}

Test-Case 'environment variables override .env, as in the application' {
    $file = Join-Path $sandbox 'c.env'
    Set-Content -LiteralPath $file -Value 'GEOPOC_DB_NAME=geopoc'
    $env:GEOPOC_DB_NAME = 'geopoc_override'
    try { $s = Get-GeopocSettings -EnvFile $file } finally { Remove-Item Env:\GEOPOC_DB_NAME }
    Assert-Equal $s.DbName 'geopoc_override'
}

Test-Case 'Hide-Secret removes the password from text' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'x')
    $out = Hide-Secret "connect failed for password $FakePassword at host" $s
    Assert-NotMatch $out ([regex]::Escape($FakePassword))
    Assert-Match $out '\*\*\*'
}

Test-Case 'scratch database names: only geopoc_<lowercase> qualifies' {
    foreach ($ok in 'geopoc_e2e', 'geopoc_test', 'geopoc_smoke', 'geopoc_a_1') { Assert-True (Test-ScratchDatabaseName $ok) "$ok should be scratch" }
    foreach ($bad in 'geopoc', 'Geopoc_x', 'GEOPOC_X', 'geopoc_', 'geopoc-x', '', 'geopoc_x; drop database geopoc', 'geopoc_x y', 'postgres', 'geopocx') {
        Assert-True (-not (Test-ScratchDatabaseName $bad)) "'$bad' must be treated as the working database"
        Assert-Equal (Get-DatabaseEnvironment $bad) 'working'
    }
}

Test-Case 'ConvertTo-ArgumentString quotes spaces, quotes and empty values' {
    Assert-Equal (ConvertTo-ArgumentString @('a', 'b c', '')) 'a "b c" ""'
    Assert-Equal (ConvertTo-ArgumentString @('say "hi"')) '"say \"hi\""'
}

# ============================================================================================================
Write-Host "`n--- native command runner ---"

Test-Case 'Invoke-NativeCommand returns output and exit code without throwing' {
    $ok = Invoke-NativeCommand -FilePath 'cmd.exe' -Arguments @('/c', 'echo hello') -TimeoutSeconds 20
    Assert-Equal $ok.ExitCode 0
    Assert-Match $ok.StdOut 'hello'
    $bad = Invoke-NativeCommand -FilePath 'cmd.exe' -Arguments @('/c', 'echo to-stderr 1>&2 & exit 3') -TimeoutSeconds 20
    Assert-Equal $bad.ExitCode 3
    Assert-Match $bad.StdErr 'to-stderr'
}

Test-Case 'Invoke-NativeCommand times out and reports a missing program' {
    $slow = Invoke-NativeCommand -FilePath 'ping.exe' -Arguments @('-n', '8', '127.0.0.1') -TimeoutSeconds 1
    Assert-True $slow.TimedOut 'the slow command should have timed out'
    $missing = Invoke-NativeCommand -FilePath 'definitely-not-a-program.exe' -TimeoutSeconds 5
    Assert-True (-not $missing.Started) 'a missing program is reported, not thrown'
}

# ============================================================================================================
Write-Host "`n--- backup (mocked pg_dump / pg_restore / psql) ---"

function New-BackupOps {
    param([string]$DumpMode = 'ok', [string]$ListMode = 'ok', [hashtable]$SourceCounts, [hashtable]$CopyCounts, [bool]$RestoreFails = $false)
    $calls = New-Object System.Collections.Generic.List[string]
    $ops = @{
        FindTool = { param($Name) "FAKE-$Name" }
        Now = { [datetime]::new(2026, 10, 29, 12, 0, 0, [DateTimeKind]::Utc) }
        Run = {
            param($FilePath, $Arguments, $TimeoutSeconds, $Environment)
            $calls.Add("$FilePath $($Arguments -join ' ')")
            $leaked = ($Arguments -join ' ') -match 'fake-secret-pw'
            if ($leaked) { throw 'the password appeared on a command line' }
            if ($FilePath -eq 'FAKE-pg_dump' -and $Arguments -contains '--version') { return [pscustomobject]@{ ExitCode = 0; StdOut = 'pg_dump (PostgreSQL) 18.1'; StdErr = ''; TimedOut = $false } }
            if ($FilePath -eq 'FAKE-pg_dump') {
                $out = $Arguments[[array]::IndexOf($Arguments, '-f') + 1]
                if ($DumpMode -eq 'fail') { return [pscustomobject]@{ ExitCode = 1; StdOut = ''; StdErr = 'FATAL: password authentication failed for user geopoc, password fake-secret-pw-123'; TimedOut = $false } }
                if ($DumpMode -eq 'empty') { [IO.File]::WriteAllBytes($out, [byte[]]@()); return [pscustomobject]@{ ExitCode = 0; StdOut = ''; StdErr = ''; TimedOut = $false } }
                [IO.File]::WriteAllBytes($out, [byte[]](1..200))
                return [pscustomobject]@{ ExitCode = 0; StdOut = ''; StdErr = ''; TimedOut = $false }
            }
            if ($Arguments -contains '--list') {
                if ($ListMode -eq 'bad') { return [pscustomobject]@{ ExitCode = 1; StdOut = ''; StdErr = 'not a valid archive'; TimedOut = $false } }
                return [pscustomobject]@{ ExitCode = 0; StdOut = "; Archive created`n1; 0 0 TABLE public project geopoc`n2; 0 0 TABLE DATA public project geopoc"; StdErr = ''; TimedOut = $false }
            }
            if ($RestoreFails) { return [pscustomobject]@{ ExitCode = 1; StdOut = ''; StdErr = 'pg_restore: error: could not execute query'; TimedOut = $false } }
            return [pscustomobject]@{ ExitCode = 0; StdOut = ''; StdErr = ''; TimedOut = $false }
        }.GetNewClosure()
        Psql = {
            param($Settings, $Database, $Sql)
            $calls.Add("psql[$Database] $Sql")
            if ($Sql -match 'server_version') { return @('17.11|0008') }
            if ($Sql -match 'from pg_database') { return @() }
            if ($Sql -match '^create database|^drop database') { return @() }
            if ($Sql -match 'information_schema') { return @('asset', 'project', 'worker_heartbeat') }
            if ($Sql -match 'count\(\*\)') {
                $c = if ($Database -like 'geopoc_verify_*') { $CopyCounts } else { $SourceCounts }
                return @($c.Keys | Sort-Object | ForEach-Object { "$_|$($c[$_])" })
            }
            return @()
        }.GetNewClosure()
    }
    return [pscustomobject]@{ Ops = $ops; Calls = $calls }
}

$sameCounts = @{ asset = 9; project = 1; worker_heartbeat = 8 }

Test-Case 'backup writes a uniquely named dump, SHA-256, manifest; never a .part file; password never on a command line' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'd1')
    $dir = Join-Path $sandbox 'bk1'
    $m = New-BackupOps -SourceCounts $sameCounts -CopyCounts $sameCounts
    $r = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir $dir -Ops $m.Ops
    Assert-True $r.Success "backup failed: $($r.Error)"
    Assert-True (Test-Path -LiteralPath $r.File)
    Assert-Equal @(Get-ChildItem $dir -Filter '*.part').Count 0
    Assert-Match (Split-Path -Leaf $r.File) '^geopoc_test-20261029-\d{6}-[0-9a-f]{4}\.dump$'
    Assert-Equal $r.Sha256 ((Get-FileHash -LiteralPath $r.File -Algorithm SHA256).Hash.ToLowerInvariant())
    $manifest = Get-Content -LiteralPath $r.ManifestPath -Raw | ConvertFrom-Json
    Assert-Equal $manifest.database 'geopoc_test'; Assert-Equal $manifest.verified $false; Assert-Equal $manifest.alembic_revision '0008'
    Assert-Match (Get-Content -LiteralPath "$($r.File).sha256" -Raw) $r.Sha256
}

Test-Case 'two backups in the same second get different files; an existing file is never overwritten' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'd2')
    $dir = Join-Path $sandbox 'bk2'
    $m = New-BackupOps -SourceCounts $sameCounts -CopyCounts $sameCounts
    $a = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir $dir -Ops $m.Ops
    $b = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir $dir -Ops $m.Ops
    Assert-True ($a.Success -and $b.Success)
    Assert-True ($a.File -ne $b.File) 'file names must differ'
    Assert-Equal @(Get-ChildItem $dir -Filter '*.dump').Count 2
}

Test-Case 'a failed pg_dump leaves no file, reports clearly, and hides the password' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'd3')
    $dir = Join-Path $sandbox 'bk3'
    $m = New-BackupOps -DumpMode 'fail'
    $r = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir $dir -Ops $m.Ops
    Assert-True (-not $r.Success)
    Assert-Equal @(Get-ChildItem $dir -ErrorAction SilentlyContinue).Count 0 'no file may remain after a failed dump'
    Assert-Match $r.Error 'pg_dump failed'
    Assert-NotMatch $r.Error 'fake-secret-pw' 'the password must be masked in the error'
}

Test-Case 'an empty dump is removed and reported' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'd4')
    $dir = Join-Path $sandbox 'bk4'
    $r = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir $dir -Ops (New-BackupOps -DumpMode 'empty').Ops
    Assert-True (-not $r.Success); Assert-Match $r.Error 'empty'
    Assert-Equal @(Get-ChildItem $dir -ErrorAction SilentlyContinue).Count 0
}

Test-Case 'an unreadable archive is not reported as a success' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'd5')
    $r = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir (Join-Path $sandbox 'bk5') -Ops (New-BackupOps -ListMode 'bad').Ops
    Assert-True (-not $r.Success); Assert-Match $r.Error 'not trustworthy'
}

Test-Case 'backup refuses odd database names, a missing tool and too little disk space' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'd6')
    $m = New-BackupOps
    $r = Invoke-DatabaseBackup -Settings $s -Database 'geopoc; drop database x' -OutputDir (Join-Path $sandbox 'bk6') -Ops $m.Ops
    Assert-True (-not $r.Success); Assert-Match $r.Error 'Refusing database name'
    $m.Ops.FindTool = { param($Name) $null }
    $r = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir (Join-Path $sandbox 'bk6') -Ops $m.Ops
    Assert-Match $r.Error 'pg_dump was not found'
    $r = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir (Join-Path $sandbox 'bk6') -Ops (New-BackupOps).Ops -MinFreeBytes 1PB
    Assert-Match $r.Error 'free on'
}

Test-Case '-Verify: restore into a disposable database, compare, drop it, record verified' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'd7')
    $m = New-BackupOps -SourceCounts $sameCounts -CopyCounts $sameCounts
    $r = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir (Join-Path $sandbox 'bk7') -Verify -Ops $m.Ops
    Assert-True ($r.Success -and $r.Verified) "not verified: $($r.Messages -join '; ')"
    $creates = @($m.Calls | Where-Object { $_ -match 'create database "geopoc_verify_\d{14}"' })
    $drops = @($m.Calls | Where-Object { $_ -match 'drop database if exists "geopoc_verify_\d{14}"' })
    Assert-Equal $creates.Count 1; Assert-Equal $drops.Count 1
    Assert-Equal @($m.Calls | Where-Object { $_ -match 'psql\[geopoc_test\].*(drop|truncate)' }).Count 0 'the source database must never be modified'
    $manifest = Get-Content -LiteralPath $r.ManifestPath -Raw | ConvertFrom-Json
    Assert-Equal $manifest.verified $true; Assert-Equal $manifest.verification.temporary_database_dropped $true
}

Test-Case '-Verify: a difference between source and restored copy means NOT verified' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'd8')
    $m = New-BackupOps -SourceCounts $sameCounts -CopyCounts @{ asset = 8; project = 1; worker_heartbeat = 99 }
    $r = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir (Join-Path $sandbox 'bk8') -Verify -Ops $m.Ops
    Assert-True $r.Success; Assert-True (-not $r.Verified)
    $manifest = Get-Content -LiteralPath $r.ManifestPath -Raw | ConvertFrom-Json
    Assert-Equal @($manifest.verification.differences).Count 1 'only the asset difference counts: worker_heartbeat is never compared'
    Assert-Match $manifest.verification.differences[0] '^asset:'
}

Test-Case '-Verify: a failed restore is NOT verified and the temporary database is still dropped' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'd9')
    $m = New-BackupOps -SourceCounts $sameCounts -CopyCounts $sameCounts -RestoreFails $true
    $r = Invoke-DatabaseBackup -Settings $s -Database 'geopoc_test' -OutputDir (Join-Path $sandbox 'bk9') -Verify -Ops $m.Ops
    Assert-True (-not $r.Verified)
    Assert-Equal @($m.Calls | Where-Object { $_ -match 'drop database if exists "geopoc_verify_' }).Count 1
}

Test-Case 'Test-RecentVerifiedBackup accepts a good backup and rejects every bad one' {
    $ops = (New-BackupOps).Ops
    $dir = Join-Path $sandbox 'bk10'
    $good = New-FakeBackup -Dir $dir -AgeHours 2
    $r = Test-RecentVerifiedBackup -Database 'geopoc' -ManifestPath $good -Ops $ops
    Assert-True $r.Ok "good backup rejected: $($r.Reason)"
    $cases = @(
        @{ Name = 'unverified'; Path = (New-FakeBackup -Dir $dir -Verified $false); Match = 'not verified' },
        @{ Name = 'stale'; Path = (New-FakeBackup -Dir $dir -AgeHours 30); Match = 'hours old' },
        @{ Name = 'future'; Path = (New-FakeBackup -Dir $dir -AgeHours -5); Match = 'future' },
        @{ Name = 'other database'; Path = (New-FakeBackup -Dir $dir -Database 'geopoc_test'); Match = "not 'geopoc'" },
        @{ Name = 'tampered dump'; Path = (New-FakeBackup -Dir $dir -TamperDump); Match = 'SHA-256|size' },
        @{ Name = 'missing dump'; Path = (New-FakeBackup -Dir $dir -MissingDump); Match = 'missing' },
        @{ Name = 'no manifest'; Path = (Join-Path $dir 'nope.manifest.json'); Match = 'not found' }
    )
    foreach ($c in $cases) {
        $r = Test-RecentVerifiedBackup -Database 'geopoc' -ManifestPath $c.Path -Ops $ops
        Assert-True (-not $r.Ok) "$($c.Name) was accepted"
        Assert-Match $r.Reason $c.Match "case $($c.Name)"
    }
    $empty = Test-RecentVerifiedBackup -Database 'geopoc' -BackupRoot (Join-Path $sandbox 'no-such-folder') -Ops $ops
    Assert-True (-not $empty.Ok); Assert-Match $empty.Reason 'No backup manifest'
}

Test-Case 'the newest manifest for the database is found automatically' {
    $dir = Join-Path $sandbox 'bk11'
    [void](New-FakeBackup -Dir $dir -AgeHours 20)
    $newest = New-FakeBackup -Dir $dir -AgeHours 1
    [void](New-FakeBackup -Dir $dir -AgeHours 5 -Database 'geopoc_test')
    Assert-Equal (Get-LatestBackupManifest -Database 'geopoc' -BackupRoot $dir) $newest
}

# ============================================================================================================
Write-Host "`n--- reset guards (synthetic settings, sandbox data, mocked database) ---"

function New-ResetScenario {
    $root = New-DataRoot -Path (Join-Path $sandbox ('work-' + [guid]::NewGuid().ToString('N').Substring(0, 6)))
    $settings = New-FakeSettings -DataRoot $root
    $backupDir = Join-Path $sandbox ('rb-' + [guid]::NewGuid().ToString('N').Substring(0, 6))
    $quarantine = Join-Path $sandbox ('q-' + [guid]::NewGuid().ToString('N').Substring(0, 6))
    return [pscustomobject]@{ Root = $root; Settings = $settings; BackupDir = $backupDir; Quarantine = $quarantine }
}
function Get-Phrase($scenario) { Get-ResetPhrase -Working $true -Database 'geopoc' -DataRootDisplay $scenario.Settings.DataRoot }
function Test-Untouched($scenario) {
    Assert-Equal @(Get-ChildItem (Join-Path $scenario.Root 'originals') -Recurse -File).Count 3 'originals must be untouched'
    Assert-Equal @(Get-ChildItem (Join-Path $scenario.Root 'previews') -File).Count 2 'previews must be untouched'
    Assert-True (-not (Test-Path -LiteralPath $scenario.Quarantine)) 'no quarantine folder may exist'
}
function Assert-NoTruncate($mock) { Assert-Equal @($mock.Log | Where-Object { $_ -match '::\s*truncate' }).Count 0 'the database must not be emptied' }

Test-Case 'default is a dry run: reports counts and files, changes nothing' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $r = Invoke-DataReset -Settings $sc.Settings -QuarantineRoot $sc.Quarantine -BackupRoot $sc.BackupDir -Ops $m.Ops
    Assert-Equal $r.Outcome 'dry-run'
    $text = $r.Report -join "`n"
    Assert-Match $text 'Target database : geopoc \(WORKING database\)'
    Assert-Match $text 'asset\s+3'; Assert-Match $text 'audit_event\s+5'
    Assert-Match $text 'originals\s+3 files'; Assert-Match $text 'previews\s+2 files'
    Assert-Match $text 'DRY RUN: nothing was changed'
    Assert-Match $text '\[NO\] working-data authorization'
    Assert-NoTruncate $m; Test-Untouched $sc
}

Test-Case 'the working database is refused without the separate authorization' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $r = Invoke-DataReset -Settings $sc.Settings -Execute -Confirmation (Get-Phrase $sc) -QuarantineRoot $sc.Quarantine -BackupRoot $sc.BackupDir -Ops $m.Ops
    Assert-Equal $r.Outcome 'refused'
    Assert-Match ($r.Failures -join ' ') 'working-data authorization'
    Assert-NoTruncate $m; Test-Untouched $sc
}

Test-Case '-Force cannot bypass any protection for the working database' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $r = Invoke-DataReset -Settings $sc.Settings -Execute -Force -AuthorizeWorkingData -QuarantineRoot $sc.Quarantine -BackupRoot $sc.BackupDir -Ops $m.Ops
    Assert-Equal $r.Outcome 'refused'
    $failures = $r.Failures -join ' | '
    Assert-Match $failures 'full confirmation phrase'; Assert-Match $failures 'recent verified backup'
    Assert-Match ($r.Warnings -join ' ') '-Force is ignored'
    Assert-NoTruncate $m; Test-Untouched $sc
}

Test-Case 'a wrong or partial confirmation phrase is refused (even with everything else in place)' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $backup = New-FakeBackup -Dir $sc.BackupDir
    foreach ($phrase in @('', 'YES', 'ERASE ALL DATA IN DATABASE geopoc', (Get-Phrase $sc).ToLowerInvariant(), ((Get-Phrase $sc) + ' '))) {
        $r = Invoke-DataReset -Settings $sc.Settings -Execute -AuthorizeWorkingData -Confirmation $phrase -BackupManifest $backup -QuarantineRoot $sc.Quarantine -BackupRoot $sc.BackupDir -Ops $m.Ops
        Assert-Equal $r.Outcome 'refused' "phrase '$phrase' was accepted"
    }
    Assert-NoTruncate $m; Test-Untouched $sc
}

Test-Case 'running API, worker or web app blocks the reset' {
    $sc = New-ResetScenario; $m = New-ResetOps -ServicesRunning $true
    $backup = New-FakeBackup -Dir $sc.BackupDir
    $r = Invoke-DataReset -Settings $sc.Settings -Execute -AuthorizeWorkingData -Confirmation (Get-Phrase $sc) -BackupManifest $backup -QuarantineRoot $sc.Quarantine -BackupRoot $sc.BackupDir -Ops $m.Ops
    Assert-Equal $r.Outcome 'refused'; Assert-Match ($r.Failures -join ' ') 'still running'
    Assert-NoTruncate $m; Test-Untouched $sc
}

Test-Case 'a missing, unverified, stale, tampered or other-database backup blocks the reset' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $cases = @{
        'none' = $null
        'unverified' = (New-FakeBackup -Dir $sc.BackupDir -Verified $false)
        'stale' = (New-FakeBackup -Dir $sc.BackupDir -AgeHours 48)
        'tampered' = (New-FakeBackup -Dir $sc.BackupDir -TamperDump)
        'other database' = (New-FakeBackup -Dir $sc.BackupDir -Database 'geopoc_test')
    }
    foreach ($name in $cases.Keys) {
        $arguments = @{ Settings = $sc.Settings; Execute = $true; AuthorizeWorkingData = $true; Confirmation = (Get-Phrase $sc); QuarantineRoot = $sc.Quarantine; BackupRoot = (Join-Path $sandbox 'empty-backup-root'); Ops = $m.Ops }
        if ($cases[$name]) { $arguments.BackupManifest = $cases[$name] }
        $r = Invoke-DataReset @arguments
        Assert-Equal $r.Outcome 'refused' "backup case '$name' was accepted"
    }
    Assert-NoTruncate $m; Test-Untouched $sc
}

Test-Case 'the database name must match the application configuration; -DataRoot cannot redirect the working reset' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $backup = New-FakeBackup -Dir $sc.BackupDir
    $r = Invoke-DataReset -Settings $sc.Settings -Database 'geopocx' -Execute -AuthorizeWorkingData -Confirmation 'x' -BackupManifest $backup -QuarantineRoot $sc.Quarantine -Ops $m.Ops
    Assert-Equal $r.Outcome 'refused'; Assert-Match ($r.Failures -join ' ') 'does not match the configured database'
    $other = Join-Path $sandbox 'some-other-root'
    $r = Invoke-DataReset -Settings $sc.Settings -DataRoot $other -Execute -AuthorizeWorkingData -Confirmation (Get-Phrase $sc) -BackupManifest $backup -QuarantineRoot $sc.Quarantine -Ops $m.Ops
    Assert-Equal $r.Outcome 'refused'; Assert-Match ($r.Failures -join ' ') 'not the configured data root'
    Assert-NoTruncate $m; Test-Untouched $sc
}

Test-Case 'hostile database names are refused before any database call' {
    $sc = New-ResetScenario; $m = New-ResetOps
    foreach ($name in 'geopoc; drop database geopoc', "geopoc' or '1'='1", 'geopoc_x"; --', 'a b') {
        $r = Invoke-DataReset -Settings $sc.Settings -Database $name -Execute -Force -DataRoot (Join-Path $sc.Root '_x') -Ops $m.Ops
        Assert-Equal $r.Outcome 'refused'
    }
    Assert-Equal $m.Log.Count 0 'no database call may happen for a rejected name'
}

Test-Case 'a quarantine folder on another drive is refused (it must be a move, not a copy)' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $backup = New-FakeBackup -Dir $sc.BackupDir
    $other = 'C:\geopoc-quarantine-should-never-be-created'
    $r = Invoke-DataReset -Settings $sc.Settings -Execute -AuthorizeWorkingData -Confirmation (Get-Phrase $sc) -BackupManifest $backup -QuarantineRoot $other -BackupRoot $sc.BackupDir -Ops $m.Ops
    Assert-Equal $r.Outcome 'refused'; Assert-Match ($r.Failures -join ' ') 'same drive'
    Assert-True (-not (Test-Path -LiteralPath $other)) 'nothing may be created on the other drive'
    Assert-NoTruncate $m
}

Test-Case 'authorized working reset: files are MOVED (not deleted), tables emptied, weights and other folders untouched' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $backup = New-FakeBackup -Dir $sc.BackupDir
    $r = Invoke-DataReset -Settings $sc.Settings -Execute -AuthorizeWorkingData -Confirmation (Get-Phrase $sc) -BackupManifest $backup -QuarantineRoot $sc.Quarantine -BackupRoot $sc.BackupDir -Ops $m.Ops
    Assert-Equal $r.Outcome 'done' ($r.Failures -join '; ')
    Assert-Equal @(Get-ChildItem (Join-Path $r.Quarantine 'originals') -Recurse -File).Count 3 'the originals must be in quarantine'
    Assert-Equal @(Get-ChildItem (Join-Path $r.Quarantine 'previews') -File).Count 2
    Assert-Equal @(Get-ChildItem (Join-Path $sc.Root 'originals') -Recurse -File).Count 0 'a fresh empty originals folder remains'
    Assert-True (Test-Path -LiteralPath (Join-Path $sc.Root 'tmp\uploads'))
    Assert-True (Test-Path -LiteralPath (Join-Path $sc.Root 'weights\model\w.pth')) 'weights must stay'
    Assert-True (Test-Path -LiteralPath (Join-Path $r.Quarantine 'RESET-RECORD.json'))
    $truncates = @($m.Log | Where-Object { $_ -match '::\s*truncate' })
    Assert-Equal $truncates.Count 1
    Assert-Match $truncates[0] 'public\."asset", public\."audit_event", public\."project"'
    Assert-NotMatch $truncates[0] 'alembic_version|spatial_ref_sys'
}

Test-Case 'if emptying the database fails, the moved folders are put back' {
    $sc = New-ResetScenario; $m = New-ResetOps -TruncateFails $true
    $backup = New-FakeBackup -Dir $sc.BackupDir
    $r = Invoke-DataReset -Settings $sc.Settings -Execute -AuthorizeWorkingData -Confirmation (Get-Phrase $sc) -BackupManifest $backup -QuarantineRoot $sc.Quarantine -BackupRoot $sc.BackupDir -Ops $m.Ops
    Assert-Equal $r.Outcome 'failed'; Assert-Match ($r.Failures -join ' ') 'simulated truncate failure'
    Assert-Equal @(Get-ChildItem (Join-Path $sc.Root 'originals') -Recurse -File).Count 3 'originals must be back in place'
    Assert-Equal @(Get-ChildItem (Join-Path $sc.Root 'previews') -File).Count 2
}

Test-Case 'if a move fails midway, nothing stays half-moved' {
    $sc = New-ResetScenario; $count = @{ n = 0 }
    $m = New-ResetOps -Extra @{ Move = ({ param($Source, $Destination) $count.n++; if ($count.n -eq 2) { throw 'simulated move failure' }; Move-Item -LiteralPath $Source -Destination $Destination }.GetNewClosure()) }
    $backup = New-FakeBackup -Dir $sc.BackupDir
    $r = Invoke-DataReset -Settings $sc.Settings -Execute -AuthorizeWorkingData -Confirmation (Get-Phrase $sc) -BackupManifest $backup -QuarantineRoot $sc.Quarantine -BackupRoot $sc.BackupDir -Ops $m.Ops
    Assert-Equal $r.Outcome 'failed'
    Assert-Equal @(Get-ChildItem (Join-Path $sc.Root 'originals') -Recurse -File).Count 3
    Assert-NoTruncate $m
}

Test-Case 'scratch reset: needs an explicit underscore data folder, never the working data root' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $scratchRoot = New-DataRoot -Path (Join-Path $sc.Root '_e2e')
    $r = Invoke-DataReset -Settings $sc.Settings -Database 'geopoc_e2e' -Execute -Force -Ops $m.Ops -QuarantineRoot $sc.Quarantine
    Assert-Equal $r.Outcome 'refused' 'a scratch reset without -DataRoot must be refused'
    foreach ($bad in @($sc.Root, (Join-Path $sandbox 'not-underscore'), (Split-Path -Parent $sc.Root))) {
        $r = Invoke-DataReset -Settings $sc.Settings -Database 'geopoc_e2e' -DataRoot $bad -Execute -Force -Ops $m.Ops -QuarantineRoot $sc.Quarantine
        Assert-Equal $r.Outcome 'refused' "scratch data root '$bad' was accepted"
    }
    Assert-NoTruncate $m
    Assert-Equal @(Get-ChildItem (Join-Path $sc.Root 'originals') -Recurse -File).Count 3
    $r = Invoke-DataReset -Settings $sc.Settings -Database 'geopoc_e2e' -DataRoot $scratchRoot -Execute -Force -Ops $m.Ops -QuarantineRoot $sc.Quarantine
    Assert-Equal $r.Outcome 'done' ($r.Failures -join '; ')
    Assert-Equal @(Get-ChildItem (Join-Path $sc.Root 'originals') -Recurse -File).Count 3 'the WORKING data must be untouched by a scratch reset'
    Assert-Equal @(Get-ChildItem (Join-Path $r.Quarantine 'originals') -Recurse -File).Count 3
}

Test-Case 'scratch reset without -Force needs its (shorter) phrase' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $scratchRoot = New-DataRoot -Path (Join-Path $sc.Root '_e2e')
    $r = Invoke-DataReset -Settings $sc.Settings -Database 'geopoc_e2e' -DataRoot $scratchRoot -Execute -Ops $m.Ops -QuarantineRoot $sc.Quarantine
    Assert-Equal $r.Outcome 'refused'
    $r = Invoke-DataReset -Settings $sc.Settings -Database 'geopoc_e2e' -DataRoot $scratchRoot -Execute -Confirmation 'ERASE SCRATCH DATABASE geopoc_e2e' -Ops $m.Ops -QuarantineRoot $sc.Quarantine
    Assert-Equal $r.Outcome 'done'
}

Test-Case 'scratch data folder: only a direct child of the data root named _<name> is accepted' {
    $sc = New-ResetScenario; $m = New-ResetOps
    $elsewhere = New-DataRoot -Path (Join-Path $sandbox '_elsewhere')
    $nested = New-DataRoot -Path (Join-Path $sc.Root 'deeper\_e2e')
    $refused = @(
        (Join-Path $sc.Root '_'), (Join-Path $sc.Root '_.'), (Join-Path $sc.Root '_a b'), (Join-Path $sc.Root '_-x'),
        $elsewhere,                                  # starts with '_' but is not under the data root
        $nested,                                     # under the data root but not a direct child
        (Join-Path $sc.Root '..\_e2e'),              # resolves to a sibling of the data root
        (Join-Path $sc.Root '_e2e\..'),              # resolves to the data root itself
        (Join-Path $sc.Root '_E2E\..\originals')     # resolves to a real working folder
    )
    foreach ($bad in $refused) {
        $r = Invoke-DataReset -Settings $sc.Settings -Database 'geopoc_e2e' -DataRoot $bad -Execute -Force -Ops $m.Ops -QuarantineRoot $sc.Quarantine
        Assert-Equal $r.Outcome 'refused' "scratch data folder '$bad' was accepted"
    }
    Assert-NoTruncate $m
    Assert-Equal @(Get-ChildItem (Join-Path $sc.Root 'originals') -Recurse -File).Count 3 'the working data must be untouched'
    Assert-Equal @(Get-ChildItem (Join-Path $elsewhere 'originals') -Recurse -File).Count 3 'an unrelated underscore folder must be untouched'
    foreach ($good in '_smoke', '_e2e-2', '_Script_test9') {
        $sc2 = New-ResetScenario; $m2 = New-ResetOps
        $dir = New-DataRoot -Path (Join-Path $sc2.Root $good)
        $r = Invoke-DataReset -Settings $sc2.Settings -Database 'geopoc_e2e' -DataRoot $dir -Execute -Force -Ops $m2.Ops -QuarantineRoot $sc2.Quarantine
        Assert-Equal $r.Outcome 'done' "scratch data folder '$good' was refused: $($r.Failures -join '; ')"
    }
}

Test-Case 'an unreadable database stops an execute and is reported in a dry run' {
    $sc = New-ResetScenario
    $m = New-ResetOps -Extra @{ Psql = { param($Settings, $Database, $Sql) throw 'psql failed against database: connection refused' } }
    $backup = New-FakeBackup -Dir $sc.BackupDir
    $r = Invoke-DataReset -Settings $sc.Settings -QuarantineRoot $sc.Quarantine -Ops $m.Ops -BackupRoot $sc.BackupDir
    Assert-Equal $r.Outcome 'dry-run'; Assert-Match ($r.Report -join ' ') 'could not be read'
    $r = Invoke-DataReset -Settings $sc.Settings -Execute -AuthorizeWorkingData -Confirmation (Get-Phrase $sc) -BackupManifest $backup -QuarantineRoot $sc.Quarantine -BackupRoot $sc.BackupDir -Ops $m.Ops
    Assert-Equal $r.Outcome 'refused'; Test-Untouched $sc
}

# ============================================================================================================
Write-Host "`n--- start-up decisions ---"

function New-State { param([bool]$Api = $false, [bool]$Web = $false, [int[]]$ApiIds = @(), [int[]]$WorkerIds = @(), [int[]]$WebIds = @())
    [pscustomobject]@{ ApiListening = $Api; WebListening = $Web; ApiProcessIds = $ApiIds; WorkerProcessIds = $WorkerIds; WebProcessIds = $WebIds; JobProcessIds = @(); Running = @() } }

Test-Case 'Get-StartPlan starts only what is missing' {
    $p = Get-StartPlan -State (New-State)
    Assert-True ($p.StartApi -and $p.StartWorker -and $p.StartWeb)
    $p = Get-StartPlan -State (New-State -Api $true -Web $true -ApiIds @(1, 2) -WorkerIds @(3, 4) -WebIds @(5))
    Assert-True (-not ($p.StartApi -or $p.StartWorker -or $p.StartWeb)) 'nothing should start when everything runs'
    $p = Get-StartPlan -State (New-State -Api $true -ApiIds @(1, 2) -Web $true)
    Assert-True ((-not $p.StartApi) -and $p.StartWorker -and (-not $p.StartWeb))
}

Test-Case 'a service that has a process but is not listening yet is not started a second time' {
    $p = Get-StartPlan -State (New-State -ApiIds @(10, 11) -WorkerIds @(12, 13) -WebIds @(14))
    Assert-True (-not ($p.StartApi -or $p.StartWorker -or $p.StartWeb))
}

Test-Case '-SkipWeb never starts the web app; duplicates produce a warning, not another launch' {
    Assert-True (-not (Get-StartPlan -State (New-State) -SkipWeb).StartWeb)
    $p = Get-StartPlan -State (New-State -Api $true -ApiIds @(1, 2, 3, 4) -WorkerIds @(5, 6, 7, 8) -Web $true)
    Assert-Equal $p.Warnings.Count 2
    Assert-True (-not ($p.StartApi -or $p.StartWorker))
}

Test-Case 'Compare-MigrationRevision' {
    Assert-Equal (Compare-MigrationRevision -Database '0008' -Head '0008') 'current'
    Assert-Equal (Compare-MigrationRevision -Database '0007' -Head '0008') 'behind'
    Assert-Equal (Compare-MigrationRevision -Database '0009' -Head '0008') 'ahead'
    foreach ($pair in @(@('', '0008'), @('0008', ''), @($null, $null), @('abc', '0008'))) { Assert-Equal (Compare-MigrationRevision -Database $pair[0] -Head $pair[1]) 'unknown' }
}

Test-Case 'Get-DetectorStatus covers every state' {
    $model = { param($name, $available, $reason) [pscustomobject]@{ name = $name; available = $available; reason_unavailable = $reason } }
    $health = { param($running, $probe, $models) [pscustomobject]@{ worker = [pscustomobject]@{ running = $running; capabilities = $(if ($probe) { [pscustomobject]@{ probe = $probe } } else { [pscustomobject]@{ cuda_available = $true } }) }; models = $models } }
    Assert-Equal (Get-DetectorStatus -ModelHealth $null).State 'api-unreachable'
    Assert-Equal (Get-DetectorStatus -ModelHealth (& $health $false $null @())).State 'worker-not-running'
    Assert-Equal (Get-DetectorStatus -ModelHealth (& $health $true 'running' @())).State 'warming-up'
    Assert-Equal (Get-DetectorStatus -ModelHealth (& $health $true 'failed' @())).State 'probe-failed'
    Assert-Equal (Get-DetectorStatus -ModelHealth (& $health $true $null @())).State 'no-models'
    Assert-Equal (Get-DetectorStatus -ModelHealth (& $health $true $null @((& $model 'det' $true $null)))).State 'ready'
    $s = Get-DetectorStatus -ModelHealth (& $health $true $null @((& $model 'det' $false 'weights missing')))
    Assert-Equal $s.State 'unavailable'; Assert-Match $s.Detail 'weights missing'
}

# ============================================================================================================
Write-Host "`n--- smoke-test plans ---"

Test-Case 'the scratch smoke plan uses only geopoc_smoke, its own folder and port' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'sm1')
    $p = Get-SmokeScratchPlan -Settings $s
    Assert-Equal $p.Failures.Count 0 ($p.Failures -join '; ')
    Assert-Equal $p.Environment['GEOPOC_DB_NAME'] 'geopoc_smoke'; Assert-Equal $p.Environment['GEOPOC_DATA_SUBDIR'] '_smoke'
    Assert-Equal $p.Environment['GEOPOC_API_PORT'] '8020'; Assert-Equal $p.Environment['GEOPOC_ALLOW_STUB_MODELS'] 'true'
    Assert-Equal $p.Environment['GEOPOC_API_HOST'] '127.0.0.1'
    Assert-Match $p.DataRoot '\\_smoke$'
}

Test-Case 'the scratch smoke plan refuses the working database, working ports and an unsafe data folder' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'sm2')
    foreach ($database in 'geopoc', 'postgres', 'smoke', 'Geopoc_smoke') { Assert-True ((Get-SmokeScratchPlan -Settings $s -Database $database).Failures.Count -gt 0) "database $database accepted" }
    foreach ($port in 8000, 5173, 8010, 5174, 80, 70000) { Assert-True ((Get-SmokeScratchPlan -Settings $s -Port $port).Failures.Count -gt 0) "port $port accepted" }
    Assert-True ((Get-SmokeScratchPlan -Settings $s -DataSubdir 'smoke').Failures.Count -gt 0)
    $configuredScratch = New-FakeSettings -DbName 'geopoc_smoke' -DataRoot (Join-Path $sandbox 'sm2')
    Assert-True ((Get-SmokeScratchPlan -Settings $configuredScratch).Failures.Count -gt 0) 'the scratch database must differ from the configured one'
}

Test-Case 'the working smoke plan accepts only this machine' {
    $s = New-FakeSettings -DataRoot (Join-Path $sandbox 'sm3')
    Assert-Equal (Get-SmokeWorkingPlan -Settings $s).Failures.Count 0
    Assert-Equal (Get-SmokeWorkingPlan -Settings $s -Url 'http://localhost:8000').Failures.Count 0
    foreach ($url in 'http://10.0.0.5:8000', 'http://example.com', 'not a url') { Assert-True ((Get-SmokeWorkingPlan -Settings $s -Url $url).Failures.Count -gt 0) "$url accepted" }
}

# ============================================================================================================
Write-Host "`n--- static checks of the scripts ---"

function Read-Script { param([string]$Relative) Get-Content -LiteralPath (Join-Path $repo $Relative) -Raw }

Test-Case 'only migrate.ps1 can apply migrations; start-up never does' {
    foreach ($file in 'scripts\dev.ps1', 'scripts\stop.ps1', 'scripts\smoke.ps1', 'scripts\backup-db.ps1', 'scripts\reset-working-data.ps1', 'scripts\lib\startup.ps1', 'scripts\lib\reset.ps1', 'scripts\lib\backup.ps1', 'scripts\lib\common.ps1', 'scripts\lib\smoke.ps1') {
        Assert-NotMatch (Read-Script $file) '(?i)alembic.{0,20}upgrade' "$file must not apply migrations"
    }
    Assert-Match (Read-Script 'scripts\migrate.ps1') '(?i)alembic.{0,40}upgrade'
    Assert-Match (Read-Script 'scripts\migrate.ps1') 'Test-RecentVerifiedBackup'
}

Test-Case 'pytest.ps1 never sets PYTEST_ADDOPTS (it would leak into the session); the plugin switch is an argument' {
    $text = Read-Script 'scripts\pytest.ps1'
    $code = (($text -split "`n") | Where-Object { $_.TrimStart() -notmatch '^#' }) -join "`n"
    Assert-NotMatch $code '\$env:PYTEST_ADDOPTS\s*=' 'pytest.ps1 must not assign PYTEST_ADDOPTS'
    Assert-NotMatch $code 'SetEnvironmentVariable\(\s*.PYTEST_ADDOPTS' 'pytest.ps1 must not assign PYTEST_ADDOPTS'
    Assert-Match $code "'no:schemathesis'" 'the plugin switch must be passed as an argument'
}

Test-Case 'migrate.ps1 does not read $backup unless it was set (scratch failure message)' {
    $text = Read-Script 'scripts\migrate.ps1'
    Assert-Match $text '(?m)^\$backup = \$null' '$backup must be initialised before the working-database branch'
    Assert-Match $text 'no backup was required for this scratch database'
    Assert-NotMatch $text 'verified backup is \$\(\$backup\.ManifestPath\)\.' 'the failure message must not dereference $backup unconditionally'
}

Test-Case 'every uv call uses --no-sync' {
    foreach ($file in Get-ChildItem (Join-Path $repo 'scripts') -Recurse -Filter *.ps1) {
        if ($file.FullName -like '*\tests\*') { continue }
        $text = Get-Content -LiteralPath $file.FullName -Raw
        $plain = [regex]::Matches($text, 'uv run (?!--no-sync)')                 # uv run ...
        $array = [regex]::Matches($text, "@\('run',(?!\s*'--no-sync')")          # @('run', ...) argument lists
        if ($plain.Count + $array.Count -gt 0) { throw "$($file.Name): a 'uv run' call without --no-sync" }
    }
}

Test-Case 'the reset code can only move files: no Remove-Item, no direct delete' {
    $text = (Read-Script 'scripts\lib\reset.ps1') + (Read-Script 'scripts\reset-working-data.ps1')
    Assert-NotMatch $text '(?i)Remove-Item' 'the reset must quarantine, never delete'
    Assert-NotMatch $text '(?i)\[IO\.(Directory|File)\]::Delete|rmdir|rd /s' 'no other delete path'
    Assert-NotMatch $text '(?i)docker exec' 'database access goes through Invoke-Psql only'
}

Test-Case 'the database password never travels on a command line' {
    foreach ($file in Get-ChildItem (Join-Path $repo 'scripts') -Recurse -Filter *.ps1) {
        if ($file.FullName -like '*\tests\*') { continue }
        # Comments may mention these option names; only code is checked.
        $text = (Get-Content -LiteralPath $file.FullName | Where-Object { $_.TrimStart() -notlike '#*' }) -join "`n"
        Assert-NotMatch $text '(?i)(-W\b|--password[ =]|PGPASSWORD=)' "$($file.Name) passes a password as an argument"
    }
    Assert-Match (Read-Script 'scripts\lib\backup.ps1') 'PGPASSWORD = \$Settings\.GetDbPassword\(\)'
}

Test-Case 'dev.ps1 reports migrations but never applies them, and is safe to run twice' {
    $text = Read-Script 'scripts\dev.ps1'
    Assert-Match $text 'Get-StartPlan'; Assert-Match $text 'already running; not started again'
    Assert-Match $text 'migrate\.ps1'
    Assert-Match $text 'Wait-Until'
}

Test-Case 'the README no longer presents the reset as routine' {
    $readme = Read-Script 'README.md'
    Assert-NotMatch $readme '(?i)reset-working-data\.ps1`?\s+empties the working database' 'old wording is still present'
    Assert-Match $readme '(?i)reset-working-data'
    Assert-Match $readme '(?i)dry run|dry-run'
    Assert-Match $readme 'runbook'
}

# ============================================================================================================
Write-Host "`n--- integration (scratch database geopoc_script_test; needs Docker) ---"

$integrationDb = 'geopoc_script_test'
if ($SkipIntegration) { Skip-Case 'integration tests' '-SkipIntegration was given' }
elseif (-not (Test-DockerEngine).Available) { Skip-Case 'integration tests' 'the Docker engine is not running' }
else {
    $real = Get-GeopocSettings
    if (-not (Test-ScratchDatabaseName $integrationDb) -or $integrationDb -ceq $real.DbName) { throw 'refusing to run integration tests: the test database name is not a safe scratch name' }
    $psql = { param($sql, $db = 'postgres') Invoke-Psql -Settings $real -Database $db -Sql $sql }
    [void](& $psql "drop database if exists ""$integrationDb"" with (force)")
    [void](& $psql "create database ""$integrationDb""")
    $integrationRoot = Join-Path $sandbox 'integration'
    try {
        Test-Case 'migrate.ps1: dry run changes nothing, -Apply migrates a scratch database without a backup' {
            $dry = Invoke-NativeCommand -FilePath 'powershell.exe' -Arguments @('-NoProfile', '-File', (Join-Path $repo 'scripts\migrate.ps1'), '-Database', $integrationDb) -TimeoutSeconds 300
            Assert-Equal $dry.ExitCode 0 $dry.StdErr
            Assert-Match $dry.StdOut 'behind'; Assert-Match $dry.StdOut 'DRY RUN'
            Assert-True (@(& $psql "select 1 from information_schema.tables where table_name = 'alembic_version'" $integrationDb).Count -eq 0) 'a dry run must not create tables'
            $apply = Invoke-NativeCommand -FilePath 'powershell.exe' -Arguments @('-NoProfile', '-File', (Join-Path $repo 'scripts\migrate.ps1'), '-Database', $integrationDb, '-Apply') -TimeoutSeconds 600
            Assert-Equal $apply.ExitCode 0 ($apply.StdOut + $apply.StdErr)
            Assert-Match $apply.StdOut 'revision is now \d{4}'
            Assert-True (@(& $psql 'select version_num from alembic_version' $integrationDb).Count -eq 1)
        }
        Test-Case 'the backup guard used by migrate.ps1 refuses when no backup exists' {
            # migrate.ps1 itself is deliberately NOT run against the working database here; its guard function is.
            $emptyRoot = Join-Path $integrationRoot 'empty-backups'
            New-Item -ItemType Directory -Force -Path $emptyRoot | Out-Null
            $headCheck = Test-RecentVerifiedBackup -Database $real.DbName -BackupRoot $emptyRoot
            Assert-True (-not $headCheck.Ok) 'with no backup the guard must refuse'
            Assert-Match $headCheck.Reason 'No backup manifest'
        }
        Test-Case 'backup-db: real pg_dump, SHA-256, restore verification into a disposable database, then it is dropped' {
            [void](& $psql "insert into workspace_user (id, display_name, created_at) values ('00000000-0000-4000-8000-0000000000aa', 'Script Test', now())" $integrationDb)
            [void](& $psql "insert into audit_event (id, at, actor_id, action, entity_type, request_id, details) values (gen_random_uuid(), now(), '00000000-0000-4000-8000-0000000000aa', 'test.event', 'test', 'r1', '{}')" $integrationDb)
            $out = Join-Path $integrationRoot 'backups'
            $r = Invoke-DatabaseBackup -Settings $real -Database $integrationDb -OutputDir $out -Verify
            Assert-True $r.Success "backup failed: $($r.Error)"
            Assert-True $r.Verified "not verified: $($r.Messages -join '; ')"
            Assert-Equal $r.Sha256 ((Get-FileHash -LiteralPath $r.File -Algorithm SHA256).Hash.ToLowerInvariant())
            $manifest = Get-Content -LiteralPath $r.ManifestPath -Raw | ConvertFrom-Json
            Assert-True ($manifest.verification.tables_compared -ge 20) 'all tables should be compared'
            Assert-Equal $manifest.verification.temporary_database_dropped $true
            Assert-Equal @(& $psql "select datname from pg_database where datname like 'geopoc_verify_%'").Count 0 'the disposable database must be gone'
            $recent = Test-RecentVerifiedBackup -Database $integrationDb -BackupRoot $out
            Assert-True $recent.Ok $recent.Reason
        }
        Test-Case 'backup-db: a database that does not exist fails clearly and leaves no file' {
            $out = Join-Path $integrationRoot 'backups-missing'
            $r = Invoke-DatabaseBackup -Settings $real -Database 'geopoc_no_such_database' -OutputDir $out
            Assert-True (-not $r.Success); Assert-Match $r.Error 'pg_dump failed'
            Assert-Equal @(Get-ChildItem $out -ErrorAction SilentlyContinue).Count 0
        }
        Test-Case 'reset on a real scratch database: dry run reads counts; -Force moves files and empties every application table' {
            # A copy of the real settings whose data root is the sandbox, so the scratch folder is a direct child of it and the
            # real data root (D:\geopoc-data) is never involved.
            $sandboxSettings = $real.PSObject.Copy()
            $sandboxSettings.DataRootPath = [IO.Path]::GetFullPath($integrationRoot)
            $sandboxSettings.DataRoot = $integrationRoot.Replace('\', '/')
            $dataRoot = New-DataRoot -Path (Join-Path $integrationRoot '_scratchdata')
            $dry = Invoke-DataReset -Settings $sandboxSettings -Database $integrationDb -DataRoot $dataRoot -QuarantineRoot (Join-Path $integrationRoot 'q')
            Assert-Equal $dry.Outcome 'dry-run'
            Assert-Match ($dry.Report -join "`n") 'audit_event\s+1'
            Assert-Equal @(& $psql 'select count(*) from audit_event' $integrationDb)[0] '1' 'a dry run must not change data'
            $run = Invoke-DataReset -Settings $sandboxSettings -Database $integrationDb -DataRoot $dataRoot -QuarantineRoot (Join-Path $integrationRoot 'q') -Execute -Force
            Assert-Equal $run.Outcome 'done' ($run.Failures -join '; ')
            $tables = @(& $psql "select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE' and table_name not in ('alembic_version','spatial_ref_sys')" $integrationDb)
            foreach ($t in $tables) { Assert-Equal @(& $psql "select count(*) from public.""$t""" $integrationDb)[0] '0' "table $t was not emptied" }
            Assert-Equal @(& $psql 'select count(*) from alembic_version' $integrationDb)[0] '1' 'the schema revision must be kept'
            Assert-Equal @(Get-ChildItem (Join-Path $run.Quarantine 'originals') -Recurse -File).Count 3
        }
        Test-Case 'a working-named target is refused when executed with -Force but without authorization (mocked database, sandbox files)' {
            $work = New-ResetOps
            $probe = New-FakeSettings -DbName $real.DbName -DataRoot (Join-Path $integrationRoot 'pretend-working')
            $r = Invoke-DataReset -Settings $probe -Execute -Force -Ops $work.Ops
            Assert-Equal $r.Outcome 'refused'
            Assert-NoTruncate $work
        }
    }
    finally {
        [void](& $psql "drop database if exists ""$integrationDb"" with (force)")
    }
}

# ============================================================================================================
$sandboxResolved = [IO.Path]::GetFullPath($sandbox)
if ($sandboxResolved.StartsWith($sandboxBase + '\', [StringComparison]::OrdinalIgnoreCase)) {
    Get-ChildItem -LiteralPath $sandboxResolved -Recurse -Force -File -ErrorAction SilentlyContinue | ForEach-Object { $_.IsReadOnly = $false }
    Remove-Item -LiteralPath $sandboxResolved -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "`n=== $script:Passed passed, $($script:Failures.Count) failed, $script:Skipped skipped ==="
foreach ($failure in $script:Failures) { Write-Host "  FAILED: $failure" -ForegroundColor Red }
if ($script:Failures.Count -gt 0) { exit 1 }
exit 0
