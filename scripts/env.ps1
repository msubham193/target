# Dot-source this file to keep everything large on D: for the current session:
#   . .\scripts\env.ps1
# C: is nearly full, so package caches and the Python environment (several GB with PyTorch) live on D:.
# The source code stays in this folder.
$env:UV_CACHE_DIR = 'D:\geopoc-cache\uv'
$env:UV_PROJECT_ENVIRONMENT = 'D:\geopoc-cache\venv-api'
$env:npm_config_store_dir = 'D:\geopoc-cache\pnpm'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\geopoc-cache\playwright'
