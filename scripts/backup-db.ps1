# Backs up a database with pg_dump into a NEW file (never overwrites), records its SHA-256, and can verify it by
# restoring into a disposable database and comparing table contents.
#
#   .\scripts\backup-db.ps1 -Verify                   backup of the configured (working) database, verified
#   .\scripts\backup-db.ps1                           backup only; the manifest says "verified: false"
#   .\scripts\backup-db.ps1 -Database geopoc_test     a scratch database
#   .\scripts\backup-db.ps1 -OutputDir E:\backups     another folder
#
# Output (default D:\geopoc-backups\db):  <db>-<yyyyMMdd-HHmmss>-<xxxx>.dump, .dump.sha256 and .dump.manifest.json
# The reset and migrate scripts accept only a verified backup that is recent and still matches its recorded SHA-256.
# The database password comes from .env (or the environment) and never appears on a command line or in output.
param(
    [string]$Database,
    [string]$OutputDir = 'D:\geopoc-backups\db',
    [switch]$Verify,
    [switch]$KeepVerifyDatabase
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\env.ps1"
. "$PSScriptRoot\lib\common.ps1"
. "$PSScriptRoot\lib\backup.ps1"

$settings = Get-GeopocSettings
if (-not $settings.EnvFileFound) { Write-Host "Missing .env (copy .env.example and set the password)." -ForegroundColor Red; exit 1 }
if (-not $Database) { $Database = $settings.DbName }

Write-Host "Backing up database '$Database' ($(Get-DatabaseEnvironment $Database)) from $($settings.DbHost):$($settings.DbPort) ..."
$result = Invoke-DatabaseBackup -Settings $settings -Database $Database -OutputDir $OutputDir -Verify:$Verify -KeepVerifyDatabase:$KeepVerifyDatabase
foreach ($message in $result.Messages) { Write-Host "  note: $message" -ForegroundColor Yellow }
if (-not $result.Success) {
    Write-Host "BACKUP FAILED: $($result.Error)" -ForegroundColor Red
    exit 1
}
Write-Host "File     : $($result.File)"
Write-Host "Size     : $(Format-Bytes $result.Bytes)"
Write-Host "SHA-256  : $($result.Sha256)"
Write-Host "Manifest : $($result.ManifestPath)"
if ($Verify) {
    if ($result.Verified) { Write-Host "Verified : yes - restored into a disposable database and every table matched." -ForegroundColor Green }
    else { Write-Host "Verified : NO - the backup file exists but could not be proven good. Do not rely on it." -ForegroundColor Red; exit 2 }
}
else { Write-Host "Verified : no (run again with -Verify before a reset or a migration)." -ForegroundColor Yellow }
