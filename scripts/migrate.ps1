# The only supported way to apply database migrations. Start-up scripts never do it.
#
#   .\scripts\migrate.ps1                      dry run: shows the database revision, the code's head, and what would happen
#   .\scripts\migrate.ps1 -Apply               applies pending migrations to the configured database
#   .\scripts\migrate.ps1 -Apply -BackupManifest D:\geopoc-backups\db\<name>.dump.manifest.json
#
# Applying to the working database requires a VERIFIED backup (scripts\backup-db.ps1 -Verify) that is recent and
# still matches its SHA-256. Scratch databases (geopoc_*) do not need one. Migrations are not reversed automatically.
param(
    [string]$Database,
    [switch]$Apply,
    [string]$BackupManifest,
    [int]$MaxBackupAgeHours = 24,
    [string]$BackupRoot = 'D:\geopoc-backups\db'
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\env.ps1"
. "$PSScriptRoot\lib\common.ps1"
. "$PSScriptRoot\lib\backup.ps1"
. "$PSScriptRoot\lib\startup.ps1"

$settings = Get-GeopocSettings
if (-not $Database) { $Database = $settings.DbName }
if ($Database -notmatch '^[A-Za-z0-9_]+$') { Write-Host "Refusing database name '$Database'." -ForegroundColor Red; exit 1 }
$working = -not (Test-ScratchDatabaseName $Database)
if ($working -and $Database -cne $settings.DbName) { Write-Host "Database '$Database' does not match the configured working database '$($settings.DbName)'. Refusing." -ForegroundColor Red; exit 1 }

$apiDir = Join-Path $script:RepoRoot 'services\api'
$head = Get-MigrationHead -ApiDirectory $apiDir -Environment @{ GEOPOC_DB_NAME = $Database }
$current = Get-DatabaseRevision -Settings $settings -Database $Database
$state = Compare-MigrationRevision -Database $current -Head $head
Write-Host "Database : $Database ($(if ($working) { 'WORKING' } else { 'scratch' }))"
Write-Host "Revision : database $current, code head $head -> $state"

if ($state -eq 'current') { Write-Host "Nothing to apply."; exit 0 }
if ($state -eq 'ahead') { Write-Host "The database is newer than this code. Not applying anything." -ForegroundColor Red; exit 1 }
if ($state -eq 'unknown') { Write-Host "The revisions could not be compared (is the database container running?). Not applying anything." -ForegroundColor Red; exit 1 }

if (-not $Apply) {
    Write-Host "DRY RUN: nothing was changed. Take a verified backup, then run again with -Apply."
    exit 0
}
$backup = $null
if ($working) {
    $backup = Test-RecentVerifiedBackup -Database $Database -ManifestPath $BackupManifest -BackupRoot $BackupRoot -MaxAgeHours $MaxBackupAgeHours
    if (-not $backup.Ok) {
        Write-Host "REFUSED: $($backup.Reason)" -ForegroundColor Red
        Write-Host "Run  .\scripts\backup-db.ps1 -Verify  first." -ForegroundColor Yellow
        exit 1
    }
    Write-Host ("Using verified backup {0} ({1:N1} h old)." -f (Split-Path -Leaf $backup.ManifestPath), $backup.AgeHours)
}
Write-Host "Applying migrations to '$Database' ..."
$run = Invoke-NativeCommand -FilePath 'uv' -Arguments @('run', '--no-sync', 'python', '-m', 'alembic', 'upgrade', 'head') -TimeoutSeconds 600 -Environment @{ GEOPOC_DB_NAME = $Database } -WorkingDirectory $apiDir
($run.StdOut + $run.StdErr).Split("`n") | ForEach-Object { if ($_.Trim()) { Write-Host "  $($_.Trim())" } }
if ($run.ExitCode -ne 0) {
    $backupNote = if ($backup -and $backup.ManifestPath) { "the verified backup is $($backup.ManifestPath)" } else { 'no backup was required for this scratch database' }
    Write-Host "MIGRATION FAILED (exit $($run.ExitCode)). The database was NOT rolled back automatically. Inspect it before doing anything else; $backupNote." -ForegroundColor Red
    exit 1
}
$after = Get-DatabaseRevision -Settings $settings -Database $Database
Write-Host "Done. Database revision is now $after." -ForegroundColor Green
