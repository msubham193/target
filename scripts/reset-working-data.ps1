# DANGEROUS. Not a routine cleanup. Erases the application data of a database and quarantines its stored files.
# Read docs\runbook.md ("Resetting data") before using it.
#
# It does nothing unless you ask: the default is a DRY RUN that prints exactly what would be affected.
#
#   .\scripts\reset-working-data.ps1                                   dry run for the configured (working) database
#   .\scripts\reset-working-data.ps1 -Database geopoc_e2e -DataRoot D:\geopoc-data\_e2e -Execute -Force
#                                                                      a SCRATCH database: -Force skips the typed phrase
#
# Erasing the WORKING database additionally needs ALL of:
#   -Execute -AuthorizeWorkingData -Confirmation "<the full phrase the dry run prints>"
#   a recent backup made with scripts\backup-db.ps1 -Verify (-BackupManifest or the newest one in D:\geopoc-backups\db)
#   the API, worker and web app stopped (scripts\stop.ps1)
# -Force has no effect on the working database. Nothing is permanently deleted: originals, previews and tmp are MOVED
# to a timestamped folder under D:\geopoc-quarantine. The database tables are emptied (restore from the backup to undo).
param(
    [string]$Database,
    [string]$DataRoot,
    [switch]$Execute,
    [switch]$AuthorizeWorkingData,
    [switch]$Force,
    [string]$Confirmation,
    [string]$BackupManifest,
    [string]$BackupRoot = 'D:\geopoc-backups\db',
    [string]$QuarantineRoot = 'D:\geopoc-quarantine',
    [int]$MaxBackupAgeHours = 24
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\env.ps1"
. "$PSScriptRoot\lib\common.ps1"
. "$PSScriptRoot\lib\backup.ps1"
. "$PSScriptRoot\lib\reset.ps1"

$settings = Get-GeopocSettings
if (-not $settings.EnvFileFound) { Write-Host "Missing .env; nothing was done." -ForegroundColor Red; exit 1 }

# An interactive run may type the phrase instead of passing it; a non-interactive run must pass -Confirmation.
$preview = Invoke-DataReset -Settings $settings -Database $Database -DataRoot $DataRoot -AuthorizeWorkingData:$AuthorizeWorkingData -Force:$Force -Confirmation $Confirmation -BackupManifest $BackupManifest -BackupRoot $BackupRoot -QuarantineRoot $QuarantineRoot -MaxBackupAgeHours $MaxBackupAgeHours
if ($Execute -and -not $Confirmation -and -not ($Force -and -not $preview.Working) -and [Environment]::UserInteractive) {
    Write-Host ''
    Write-Host "To continue, type exactly:  $($preview.Phrase)" -ForegroundColor Yellow
    try { $Confirmation = Read-Host 'Confirmation' }
    catch { $Confirmation = '' }   # a host that cannot prompt means "no confirmation": the reset is refused
}
$result = Invoke-DataReset -Settings $settings -Database $Database -DataRoot $DataRoot -Execute:$Execute -AuthorizeWorkingData:$AuthorizeWorkingData -Force:$Force -Confirmation $Confirmation -BackupManifest $BackupManifest -BackupRoot $BackupRoot -QuarantineRoot $QuarantineRoot -MaxBackupAgeHours $MaxBackupAgeHours

foreach ($line in $result.Report) { Write-Host $line }
foreach ($warning in $result.Warnings) { Write-Host "WARNING: $warning" -ForegroundColor Yellow }
if ($result.Failures.Count -gt 0) {
    Write-Host ''
    foreach ($failure in $result.Failures) { Write-Host "  - $failure" -ForegroundColor Red }
}
switch ($result.Outcome) {
    'dry-run' { exit 0 }
    'done' { exit 0 }
    'refused' { exit 2 }
    default { exit 1 }
}
