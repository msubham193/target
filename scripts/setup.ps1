# First-time setup on a new Windows computer (the client installation). Run it once from the unpacked folder:
#
#   SETUP.cmd                              (double-click; it runs this script)
#   .\scripts\setup.ps1                    the same from PowerShell
#   .\scripts\setup.ps1 -CheckOnly         only check the computer and say what would be done; changes nothing
#   .\scripts\setup.ps1 -SkipLanguageModel do not offer to download the local language model (about 6 GB)
#
# What it does, in order: checks the required programs and free space; prepares drive D: (or maps C:\INT-TGT-storage to D:
# when the computer has no D:); creates .env with a new random database password (only if .env does not exist); asks for an
# optional Mapbox public token; installs the Python and web packages; downloads and checks the detection model; starts the
# database container; creates the database tables in a NEW, EMPTY database (revision 0009, the tested version); and offers
# to download the local language model. It is safe to run again: finished steps are skipped.
#
# It never prints the database password, never overwrites an existing .env or .env.local, and never migrates a database
# that already holds tables (use scripts\migrate.ps1 for that).
param(
    [switch]$CheckOnly,
    [switch]$SkipLanguageModel
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\lib\common.ps1"
. "$PSScriptRoot\lib\startup.ps1"

$TargetRevision = '0009'
$StorageFolder = 'C:\INT-TGT-storage'
$NeededFreeGB = 25

function Write-Step { param([string]$Text) Write-Host ''; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok { param([string]$Text) Write-Host "    OK  $Text" -ForegroundColor Green }
function Write-Note { param([string]$Text) Write-Host "    $Text" -ForegroundColor Yellow }
function Stop-Setup { param([string]$Text) Write-Host ''; Write-Host "SETUP STOPPED: $Text" -ForegroundColor Red; exit 1 }

Set-Location $script:RepoRoot
Write-Host 'INT and TGT Development - first-time setup' -ForegroundColor White
Write-Host "Folder: $script:RepoRoot"

# --- 1. Required programs ---
Write-Step 'Checking the required programs'
$tools = @(
    @{ Name = 'docker'; Label = 'Docker Desktop'; Winget = 'Docker.DockerDesktop'; Required = $true },
    @{ Name = 'uv'; Label = 'uv (Python package manager)'; Winget = 'astral-sh.uv'; Required = $true },
    @{ Name = 'node'; Label = 'Node.js'; Winget = 'OpenJS.NodeJS.LTS'; Required = $true },
    @{ Name = 'pnpm'; Label = 'pnpm'; Winget = $null; Required = $true },
    @{ Name = 'ffmpeg'; Label = 'FFmpeg (for videos)'; Winget = 'Gyan.FFmpeg'; Required = $true },
    @{ Name = 'ffprobe'; Label = 'ffprobe (comes with FFmpeg)'; Winget = 'Gyan.FFmpeg'; Required = $true },
    @{ Name = 'ollama'; Label = 'Ollama (optional: AI draft summary)'; Winget = 'Ollama.Ollama'; Required = $false }
)
$missing = @()
foreach ($tool in $tools) {
    $found = Get-Command $tool.Name -ErrorAction SilentlyContinue
    if ($found) { Write-Ok $tool.Label }
    elseif ($tool.Required) { Write-Host "    MISSING  $($tool.Label)" -ForegroundColor Red; $missing += $tool }
    else { Write-Note "not installed: $($tool.Label). Everything else works without it." }
}
$hasOllama = [bool](Get-Command ollama -ErrorAction SilentlyContinue)
if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host 'Install the missing programs, then CLOSE this window and run SETUP.cmd again:' -ForegroundColor Yellow
    foreach ($tool in $missing) {
        if ($tool.Name -eq 'pnpm') { Write-Host '    npm install -g pnpm' }
        elseif ($tool.Winget) { Write-Host "    winget install -e --id $($tool.Winget)" }
    }
    Write-Host '(The installation guide lists download pages for each program.)'
    if (-not $CheckOnly) { exit 1 }
}

# --- 2. Storage on drive D: ---
Write-Step 'Checking storage (drive D:)'
$dDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='D:'" -ErrorAction SilentlyContinue
$substitute = $false
if ($dDrive -and $dDrive.DriveType -eq 3) {
    $freeGB = [math]::Round($dDrive.FreeSpace / 1GB, 1)
    Write-Ok "D: is a local disk with $freeGB GB free"
}
elseif ($dDrive -and $dDrive.DriveType -ne 3 -and -not (Test-Path "$StorageFolder")) {
    Stop-Setup 'Drive letter D: is used by a CD/DVD, USB or network drive. Free the letter D: (Disk Management) or see the guide, section "No drive D:".'
}
else {
    $substitute = $true
    $cDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
    $freeGB = [math]::Round($cDrive.FreeSpace / 1GB, 1)
    Write-Note "No disk D:. The folder $StorageFolder will be used as D: (START.cmd maps it each time). C: has $freeGB GB free."
}
if ($freeGB -lt $NeededFreeGB) { Write-Note "About $NeededFreeGB GB is needed (PyTorch, packages, database, your files). Only $freeGB GB is free." }

if ($CheckOnly) {
    Write-Host ''
    Write-Host 'CHECK ONLY: nothing was changed.' -ForegroundColor White
    exit 0
}
if ($freeGB -lt $NeededFreeGB) { Stop-Setup "not enough free space ($freeGB GB; about $NeededFreeGB GB needed)." }

if ($substitute -and -not (Test-Path 'D:\')) {
    New-Item -ItemType Directory -Force $StorageFolder | Out-Null
    & subst.exe D: $StorageFolder
    if (-not (Test-Path 'D:\')) { Stop-Setup "could not map $StorageFolder to D:." }
    Write-Ok "$StorageFolder is now D:"
}
foreach ($folder in @('D:\geopoc-data', 'D:\geopoc-cache', 'D:\geopoc-backups\db')) { New-Item -ItemType Directory -Force $folder | Out-Null }
Write-Ok 'folders D:\geopoc-data, D:\geopoc-cache and D:\geopoc-backups are ready'
. "$PSScriptRoot\env.ps1"

# --- 3. Settings file (.env) ---
Write-Step 'Settings file (.env)'
$envFile = Join-Path $script:RepoRoot '.env'
if (Test-Path -LiteralPath $envFile) {
    Write-Ok '.env already exists; it was left unchanged'
}
else {
    $alphabet = [char[]]'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $password = -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
    $lines = Get-Content -LiteralPath (Join-Path $script:RepoRoot '.env.example')
    $lines = $lines | ForEach-Object {
        if ($_ -match '^GEOPOC_DB_PASSWORD=') { "GEOPOC_DB_PASSWORD=$password" }
        elseif ($_ -match '^# GEOPOC_LLM_ENABLED=' -and -not $hasOllama) { 'GEOPOC_LLM_ENABLED=false' }
        else { $_ }
    }
    [IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object Text.UTF8Encoding $false))
    $password = $null
    Write-Ok '.env created with a new random database password (kept only in this file; never share it)'
    if (-not $hasOllama) { Write-Note 'AI draft summary switched off (Ollama is not installed). See the guide to switch it on later.' }
}

# --- 4. Optional Mapbox token (online satellite map, 3D view, place search) ---
Write-Step 'Online satellite map (Mapbox) - optional'
$localEnv = Join-Path $script:RepoRoot 'apps\web\.env.local'
if (Test-Path -LiteralPath $localEnv) {
    Write-Ok 'apps\web\.env.local already exists; it was left unchanged'
}
else {
    Write-Host '    Paste a Mapbox PUBLIC token (it starts with pk.), or just press Enter to skip.'
    Write-Host '    Without it the app works offline with a plain map; the online 3D view and place search stay off.'
    $token = (Read-Host '    Mapbox public token').Trim()
    if ($token -eq '') { Write-Note 'skipped; you can add it later (see the guide)' }
    elseif ($token -notmatch '^pk\.[A-Za-z0-9._-]+$') { Write-Note 'that is not a public token (it must start with pk.); skipped. Never use a secret sk. token.' }
    else {
        [IO.File]::WriteAllText($localEnv, "VITE_MAPBOX_ACCESS_TOKEN=$token`n", (New-Object Text.UTF8Encoding $false))
        Write-Ok 'token saved in apps\web\.env.local'
    }
    $token = $null
}

# --- 5. Python packages and the detection model ---
Write-Step 'Installing the Python packages (several GB the first time: this can take 10-30 minutes)'
Push-Location (Join-Path $script:RepoRoot 'services\api')
try {
    & uv sync --group ml
    if ($LASTEXITCODE -ne 0) { Stop-Setup 'the Python packages could not be installed (see the messages above).' }
    Write-Ok 'Python packages installed'
    Write-Step 'Downloading and checking the detection model'
    & uv run --no-sync python -m geopoc.devtools fetch-model 'fasterrcnn_resnet50_fpn_v2@coco_v1'
    if ($LASTEXITCODE -ne 0) { Stop-Setup 'the detection model could not be downloaded or did not match its checksum.' }
    Write-Ok 'detection model ready'
}
finally { Pop-Location }

# --- 6. Web packages ---
Write-Step 'Installing the web app packages'
Push-Location (Join-Path $script:RepoRoot 'apps\web')
try {
    & pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { Stop-Setup 'the web packages could not be installed (see the messages above).' }
    Write-Ok 'web packages installed'
}
finally { Pop-Location }

# --- 7. Database ---
Write-Step 'Starting the database (Docker)'
if (-not (Test-DockerEngine).Available) {
    $desktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path -LiteralPath $desktop)) { Stop-Setup 'Docker is not running. Open Docker Desktop, wait until it says it is running, then run SETUP.cmd again.' }
    Write-Host '    Starting Docker Desktop (a minute or two)...'
    Start-Process $desktop
    if (-not (Wait-Until -Test { (Test-DockerEngine).Available } -TimeoutSeconds 300 -IntervalSeconds 5)) {
        Stop-Setup 'Docker did not become ready. Open Docker Desktop, finish its first-start questions, then run SETUP.cmd again.'
    }
}
$settings = Get-GeopocSettings
$compose = Invoke-NativeCommand -FilePath 'docker' -Arguments @('compose', '--env-file', $envFile, '-f', (Join-Path $script:RepoRoot 'infra\docker-compose.yml'), 'up', '-d', '--wait') -TimeoutSeconds 600
if ($compose.ExitCode -ne 0) { Write-Host ($compose.StdOut + $compose.StdErr); Stop-Setup 'the database container did not start.' }
Write-Ok "database running on 127.0.0.1:$($settings.DbPort)"

$current = Get-DatabaseRevision -Settings $settings -Database $settings.DbName
if ($null -eq $current) { Stop-Setup 'the database did not answer. Wait a minute and run SETUP.cmd again.' }
if ($current -eq '0000') {
    Write-Host "    New, empty database: creating the tables (revision $TargetRevision)..."
    $run = Invoke-NativeCommand -FilePath 'uv' -Arguments @('run', '--no-sync', 'python', '-m', 'alembic', 'upgrade', $TargetRevision) -TimeoutSeconds 600 -WorkingDirectory (Join-Path $script:RepoRoot 'services\api')
    if ($run.ExitCode -ne 0) { Write-Host ($run.StdOut + $run.StdErr); Stop-Setup 'the database tables could not be created.' }
    Write-Ok "tables created (revision $(Get-DatabaseRevision -Settings $settings -Database $settings.DbName))"
}
else {
    Write-Ok "the database already has tables (revision $current); not changed"
}

# --- 8. Optional local language model ---
if ($hasOllama -and -not $SkipLanguageModel) {
    Write-Step 'AI draft summary (local language model) - optional'
    $listed = Invoke-NativeCommand -FilePath 'ollama' -Arguments @('list') -TimeoutSeconds 30
    if ($listed.StdOut -match 'qwen2\.5vl') { Write-Ok 'qwen2.5vl is already downloaded' }
    else {
        $answer = (Read-Host '    Download the qwen2.5vl model now (about 6 GB)? [y/N]').Trim()
        if ($answer -match '^[Yy]') {
            & ollama pull qwen2.5vl
            if ($LASTEXITCODE -eq 0) { Write-Ok 'qwen2.5vl downloaded' } else { Write-Note 'the download did not finish; run  ollama pull qwen2.5vl  later' }
        }
        else { Write-Note 'skipped; run  ollama pull qwen2.5vl  later to use the draft summary' }
    }
}

Write-Host ''
Write-Host 'SETUP COMPLETE.' -ForegroundColor Green
Write-Host 'Start the application with START.cmd (double-click). It opens http://127.0.0.1:5173 when ready.'
if ($substitute) { Write-Host "Note: $StorageFolder is used as drive D:. START.cmd maps it again after every restart." }
