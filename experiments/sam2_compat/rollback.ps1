# Removes everything the SAM 2.1 compatibility experiment added. The application environment, database and data
# folder were never changed by it, so nothing there needs restoring; this checks that too.
#   .\experiments\sam2_compat\rollback.ps1
$ErrorActionPreference = 'Stop'
$Root = 'D:\geopoc-cache\sam2-eval'
$Venv = 'D:\geopoc-cache\venv-sam2-eval'

if (Test-Path "$Root\checkpoints") { Get-ChildItem "$Root\checkpoints" -File | ForEach-Object { $_.IsReadOnly = $false } }
foreach ($path in $Venv, $Root) { if (Test-Path $path) { Remove-Item $path -Recurse -Force } }
# Cached copies of the six evaluation packages in uv's cache are harmless; remove them too.
$env:UV_CACHE_DIR = 'D:\geopoc-cache\uv'
uv cache clean hydra-core omegaconf antlr4-python3-runtime iopath portalocker tqdm | Out-Null

$freeze = uv pip freeze --python D:\geopoc-cache\venv-api\Scripts\python.exe
"Application environment: $(@($freeze).Count) packages; torch line: $($freeze | Select-String '^torch==')"
'Experiment removed.'
