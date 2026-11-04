# Planning helpers for scripts\smoke.ps1. Dot-source after common.ps1. Pure functions: nothing is started here.

function Get-SmokeScratchPlan {
    # The scratch stack a smoke test may start: its own database, its own data folder, its own port, stub models.
    # Failures lists everything that makes the plan unsafe; the script refuses to start anything unless it is empty.
    param(
        [Parameter(Mandatory)]$Settings,
        [int]$Port = 8020,
        [string]$Database = 'geopoc_smoke',
        [string]$DataSubdir = '_smoke'
    )
    $failures = New-Object System.Collections.Generic.List[string]
    if (-not (Test-ScratchDatabaseName $Database)) { $failures.Add("'$Database' is not a scratch database name (geopoc_<name>).") }
    if ($Database -ceq 'geopoc' -or $Database -ceq $Settings.DbName) { $failures.Add("The scratch database '$Database' must not be the configured database '$($Settings.DbName)'.") }
    if ($Port -lt 1024 -or $Port -gt 65535) { $failures.Add("Port $Port is outside 1024-65535.") }
    foreach ($reserved in @($Settings.ApiPort, $Settings.WebPort, 8000, 5173, 8010, 5174)) {
        if ($Port -eq $reserved) { $failures.Add("Port $Port belongs to the working app or the browser-test stack.") }
    }
    if (-not $DataSubdir.StartsWith('_')) { $failures.Add("The scratch data folder '$DataSubdir' must start with an underscore so it can never be the working data root.") }
    $environment = @{
        GEOPOC_DB_NAME           = $Database
        GEOPOC_DATA_SUBDIR       = $DataSubdir
        GEOPOC_API_HOST          = '127.0.0.1'
        GEOPOC_API_PORT          = [string]$Port
        GEOPOC_ALLOW_STUB_MODELS = 'true'
        GEOPOC_MANIFEST_PATH     = 'tests/fixtures/manifest.stub.yaml'
        GEOPOC_JOB_POLL_INTERVAL_S = '0.2'
    }
    return [pscustomobject]@{
        Failures    = $failures.ToArray()
        Environment = $environment
        Url         = "http://127.0.0.1:$Port"
        Database    = $Database
        DataRoot    = (Join-Path $Settings.DataRootPath $DataSubdir)
    }
}

function Get-SmokeWorkingPlan {
    # Read-only checks of the running working app. Nothing is started and no write is possible.
    param([Parameter(Mandatory)]$Settings, [string]$Url)
    $target = if ($Url) { $Url } else { "http://127.0.0.1:$($Settings.ApiPort)" }
    $failures = @()
    $uri = $null
    if (-not [Uri]::TryCreate($target, [UriKind]::Absolute, [ref]$uri)) { $failures += "'$target' is not a URL." }
    elseif (@('127.0.0.1', 'localhost', '[::1]', '::1') -notcontains $uri.Host) { $failures += "Refusing host '$($uri.Host)': the smoke test only talks to this machine." }
    return [pscustomobject]@{ Failures = $failures; Url = $target }
}
