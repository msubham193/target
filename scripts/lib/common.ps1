# Shared helpers for the project scripts. Dot-source it:   . "$PSScriptRoot\lib\common.ps1"
#
# Rules this file keeps:
#   - ASCII only (Windows PowerShell 5.1 misreads UTF-8 without a byte-order mark).
#   - The database password is read into a settings object and is never written to output or logs.
#   - Native commands run through Invoke-NativeCommand, which never turns a program's stderr text into a
#     PowerShell error (docker compose writes ordinary status lines to stderr) and always has a timeout.

$script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:PostgresContainer = 'geopoc-db'
$script:ScratchNamePattern = '^geopoc_[a-z0-9_]+$'

function Read-DotEnv {
    param([Parameter(Mandatory)][string]$Path)
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $values }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $text = $line.Trim()
        if ($text -eq '' -or $text.StartsWith('#')) { continue }
        $index = $text.IndexOf('=')
        if ($index -lt 1) { continue }
        $key = $text.Substring(0, $index).Trim().ToUpperInvariant()
        $value = $text.Substring($index + 1).Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$key] = $value
    }
    return $values
}

function Get-GeopocSettings {
    # Environment variables win over .env, as they do in the application (pydantic-settings).
    param([string]$EnvFile = (Join-Path $script:RepoRoot '.env'))
    $file = Read-DotEnv -Path $EnvFile
    $pick = {
        param($key, $default)
        $fromEnvironment = [Environment]::GetEnvironmentVariable($key)
        if ($fromEnvironment) { return $fromEnvironment }
        if ($file.ContainsKey($key) -and $file[$key] -ne '') { return $file[$key] }
        return $default
    }
    $password = & $pick 'GEOPOC_DB_PASSWORD' ''
    $dataRoot = & $pick 'GEOPOC_DATA_ROOT' 'D:/geopoc-data'
    $settings = [pscustomobject]@{
        EnvFile         = $EnvFile
        EnvFileFound    = (Test-Path -LiteralPath $EnvFile)
        DbHost          = & $pick 'GEOPOC_DB_HOST' '127.0.0.1'
        DbPort          = [int](& $pick 'GEOPOC_DB_PORT' '5433')
        DbUser          = & $pick 'GEOPOC_DB_USER' 'geopoc'
        DbName          = & $pick 'GEOPOC_DB_NAME' 'geopoc'
        HasDbPassword   = ($password -ne '')
        DataRoot        = $dataRoot
        DataRootPath    = [IO.Path]::GetFullPath($dataRoot)
        ApiHost         = & $pick 'GEOPOC_API_HOST' '127.0.0.1'
        ApiPort         = [int](& $pick 'GEOPOC_API_PORT' '8000')
        WebPort         = [int](& $pick 'GEOPOC_WEB_PORT' '5173')
    }
    # A method, not a property: Format-List, Out-String and logging never show the password.
    $settings | Add-Member -MemberType ScriptMethod -Name GetDbPassword -Value ({ $password }.GetNewClosure())
    return $settings
}

function Test-ScratchDatabaseName {
    param([string]$Name)
    return ($Name -cmatch $script:ScratchNamePattern)
}

function Get-DatabaseEnvironment {
    param([string]$Name)
    if (Test-ScratchDatabaseName $Name) { return 'scratch' }
    return 'working'
}

function ConvertTo-ArgumentString {
    param([string[]]$Arguments)
    $quoted = foreach ($argument in $Arguments) {
        if ($argument -eq '') { '""' }
        elseif ($argument -match '[\s"]') { '"' + (($argument -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"' }
        else { $argument }
    }
    return ($quoted -join ' ')
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [int]$TimeoutSeconds = 60,
        [hashtable]$Environment = @{},
        [string]$StdinText = $null,
        [string]$WorkingDirectory = $null
    )
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = ConvertTo-ArgumentString $Arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    if ($WorkingDirectory) { $info.WorkingDirectory = $WorkingDirectory }
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = ($null -ne $StdinText)
    foreach ($key in $Environment.Keys) { $info.EnvironmentVariables[$key] = [string]$Environment[$key] }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try { [void]$process.Start() }
    catch {
        return [pscustomobject]@{ Started = $false; ExitCode = -1; StdOut = ''; StdErr = $_.Exception.Message; TimedOut = $false }
    }
    $out = $process.StandardOutput.ReadToEndAsync()
    $err = $process.StandardError.ReadToEndAsync()
    if ($null -ne $StdinText) {
        $process.StandardInput.Write($StdinText)
        $process.StandardInput.Close()
    }
    $finished = $process.WaitForExit($TimeoutSeconds * 1000)
    if (-not $finished) {
        try { $process.Kill() } catch { Write-Verbose 'process already ended' }
        return [pscustomobject]@{ Started = $true; ExitCode = -1; StdOut = ''; StdErr = "Timed out after $TimeoutSeconds seconds."; TimedOut = $true }
    }
    $process.WaitForExit()
    return [pscustomobject]@{ Started = $true; ExitCode = $process.ExitCode; StdOut = $out.Result; StdErr = $err.Result; TimedOut = $false }
}

function Hide-Secret {
    # Defence in depth: remove the password from any text before it is shown.
    param([string]$Text, $Settings)
    if (-not $Text) { return $Text }
    $password = $Settings.GetDbPassword()
    if ($password) { return $Text.Replace($password, '***') }
    return $Text
}

function Test-DockerEngine {
    $result = Invoke-NativeCommand -FilePath 'docker' -Arguments @('info', '--format', '{{.ServerVersion}}') -TimeoutSeconds 20
    return [pscustomobject]@{ Available = ($result.Started -and $result.ExitCode -eq 0 -and $result.StdOut.Trim() -ne ''); Detail = ($result.StdOut + $result.StdErr).Trim() }
}

function Invoke-Psql {
    # Runs SQL inside the database container (its own psql, no password on any command line). The SQL goes in on stdin.
    param(
        [Parameter(Mandatory)]$Settings,
        [Parameter(Mandatory)][string]$Database,
        [Parameter(Mandatory)][string]$Sql,
        [int]$TimeoutSeconds = 60
    )
    $arguments = @('exec', '-i', $script:PostgresContainer, 'psql', '-X', '-q', '-U', $Settings.DbUser, '-d', $Database, '-v', 'ON_ERROR_STOP=1', '-At', '-F', '|', '-f', '-')
    $result = Invoke-NativeCommand -FilePath 'docker' -Arguments $arguments -TimeoutSeconds $TimeoutSeconds -StdinText $Sql
    if ($result.ExitCode -ne 0) {
        $detail = (Hide-Secret $result.StdErr.Trim() $Settings)
        throw "psql failed against database '$Database' (exit $($result.ExitCode)): $detail"
    }
    $lines = @($result.StdOut -split "`r?`n" | Where-Object { $_ -ne '' })
    return $lines
}

function Get-ServiceState {
    # What is running right now: the API, the worker, the web dev server (and any analysis job processes).
    param([Parameter(Mandatory)]$Settings)
    $listening = { param($port) (@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)).Count -gt 0 }
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $api = @($processes | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match '-m geopoc(\s|$)' })
    $worker = @($processes | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match '-m geopoc\.jobs\.worker' })
    $jobs = @($processes | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match '-m geopoc\.jobs\.run_job' })
    $web = @($processes | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -match 'vite' -and $_.CommandLine -like "*$($script:RepoRoot)*" })
    $state = [pscustomobject]@{
        ApiListening = (& $listening $Settings.ApiPort)
        WebListening = (& $listening $Settings.WebPort)
        ApiProcessIds = @($api | ForEach-Object { $_.ProcessId })
        WorkerProcessIds = @($worker | ForEach-Object { $_.ProcessId })
        JobProcessIds = @($jobs | ForEach-Object { $_.ProcessId })
        WebProcessIds = @($web | ForEach-Object { $_.ProcessId })
        Running = @()
    }
    $running = @()
    if ($state.ApiListening -or $state.ApiProcessIds.Count -gt 0) { $running += "API (port $($Settings.ApiPort))" }
    if ($state.WorkerProcessIds.Count -gt 0) { $running += 'worker' }
    if ($state.JobProcessIds.Count -gt 0) { $running += 'analysis job process' }
    if ($state.WebListening -or $state.WebProcessIds.Count -gt 0) { $running += "web app (port $($Settings.WebPort))" }
    $state.Running = $running
    return $state
}

function Invoke-HttpGet {
    # A GET with a timeout. Returns $null instead of throwing, so callers decide what a failure means.
    param([Parameter(Mandatory)][string]$Url, [int]$TimeoutSeconds = 5)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
        return [pscustomobject]@{ Status = [int]$response.StatusCode; Content = $response.Content }
    }
    catch { return $null }
}

function Wait-Until {
    param(
        [Parameter(Mandatory)][scriptblock]$Test,
        [int]$TimeoutSeconds = 60,
        [int]$IntervalSeconds = 2
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Test) { return $true }
        Start-Sleep -Seconds $IntervalSeconds
    }
    return [bool](& $Test)
}

function Format-Bytes {
    param([double]$Bytes)
    if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N1} MB' -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ('{0:N1} KB' -f ($Bytes / 1KB)) }
    return ('{0:N0} bytes' -f $Bytes)
}

function Get-DefaultOps {
    # The operations that touch the outside world, in one table, so tests can replace them with mocks.
    return @{
        Psql     = { param($Settings, $Database, $Sql) Invoke-Psql -Settings $Settings -Database $Database -Sql $Sql }
        Run      = { param($FilePath, $Arguments, $TimeoutSeconds, $Environment) Invoke-NativeCommand -FilePath $FilePath -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds -Environment $Environment }
        Services = { param($Settings) Get-ServiceState -Settings $Settings }
        Now      = { Get-Date }
        Move     = { param($Source, $Destination) Move-Item -LiteralPath $Source -Destination $Destination }
        FindTool = { param($Name) Find-PostgresTool -Name $Name }
    }
}

function Complete-Ops {
    # Fill any operation the caller did not replace with its real implementation.
    param([hashtable]$Ops)
    $all = Get-DefaultOps
    if ($Ops) { foreach ($key in $Ops.Keys) { $all[$key] = $Ops[$key] } }
    return $all
}
