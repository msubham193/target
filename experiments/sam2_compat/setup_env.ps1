# SAM 2.1 compatibility experiment: create the ISOLATED evaluation environment and fetch the approved files.
# Runs only with -Approved, after the owner has approved the exact download list in
# docs/sam2.1-compatibility-report.md. It never touches the application environment (D:\geopoc-cache\venv-api),
# the application database, or the project's data folder.
#   .\experiments\sam2_compat\setup_env.ps1 -Approved
param([switch]$Approved)
$ErrorActionPreference = 'Stop'
if (-not $Approved) { throw 'Not approved: nothing was downloaded. See docs/sam2.1-compatibility-report.md section 4.' }

$Commit = '2b90b9f5ceec907a1c18123530e92e794ad901a4'
$Root = 'D:\geopoc-cache\sam2-eval'                       # code, checkpoints, results (not the app's weights folder)
$Venv = 'D:\geopoc-cache\venv-sam2-eval'
$AppSitePackages = 'D:\geopoc-cache\venv-api\Lib\site-packages'
$BasePython = 'C:\Users\HP\AppData\Local\Programs\Python\Python311\python.exe'   # the app venv's base interpreter
$Here = $PSScriptRoot
$Checkpoints = @(
    @{ Name = 'sam2.1_hiera_tiny.pt';  Bytes = 156008466; Sha256 = '7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69' },
    @{ Name = 'sam2.1_hiera_small.pt'; Bytes = 184416285; Sha256 = '6d1aa6f30de5c92224f8172114de081d104bbd23dd9dc5c58996f0cad5dc4d38' }
)
$env:UV_CACHE_DIR = 'D:\geopoc-cache\uv'

# 0. Record the application environment before anything else.
New-Item -ItemType Directory -Force $Root, "$Root\checkpoints", "$Root\results" | Out-Null
uv pip freeze --python D:\geopoc-cache\venv-api\Scripts\python.exe | Out-File -Encoding utf8 "$Root\results\app-freeze-before.txt"

# 1. Official code at the pinned commit: only the sam2 package and licence files (sparse, blobless, one commit).
#    Nothing is built or installed from it; the source directory is put on the evaluation environment's path.
if (-not (Test-Path "$Root\src\.git")) {
    git init -q "$Root\src"
    git -C "$Root\src" remote add origin https://github.com/facebookresearch/sam2.git
    git -C "$Root\src" sparse-checkout set --no-cone /sam2/ /LICENSE /LICENSE_cctorch /README.md /setup.py /pyproject.toml
    git -C "$Root\src" fetch -q --depth 1 --filter=blob:none origin $Commit
    git -C "$Root\src" checkout -q $Commit
}
$head = (git -C "$Root\src" rev-parse HEAD).Trim()
if ($head -ne $Commit) { throw "Checked-out commit $head is not the approved $Commit." }
git -C "$Root\src" fsck --no-progress --connectivity-only 2>&1 | Out-Null
if (Test-Path "$Root\src\sam2\_C*") { throw 'A compiled extension is present; none is expected.' }

# 2. Checkpoints from Meta's URL, verified against the published size and the SHA-256 Hugging Face lists for the
#    same files (facebook/sam2.1-hiera-tiny / -small). A mismatch deletes the file and stops.
foreach ($c in $Checkpoints) {
    $target = Join-Path "$Root\checkpoints" $c.Name
    if (-not (Test-Path $target)) {
        curl.exe -fL --retry 3 -o "$target.part" "https://dl.fbaipublicfiles.com/segment_anything_2/092824/$($c.Name)"
        Move-Item "$target.part" $target
    }
    $size = (Get-Item $target).Length
    $sha = (Get-FileHash $target -Algorithm SHA256).Hash.ToLower()
    if ($size -ne $c.Bytes -or $sha -ne $c.Sha256) { Remove-Item $target; throw "$($c.Name): size $size / SHA-256 $sha do not match." }
    Set-ItemProperty $target -Name IsReadOnly -Value $true
    "$($c.Name) verified: $size bytes, SHA-256 $sha"
}

# 3. A separate virtual environment from the same base interpreter. It sees the app's packages read-only through a
#    .pth file (so torch is not installed a second time) and gets only the six small packages listed in
#    requirements-sam2-eval.txt, installed by exact file hash with no dependency resolution.
if (-not (Test-Path "$Venv\Scripts\python.exe")) { uv venv -q --python $BasePython $Venv }
@("$Root\src", $AppSitePackages) | Out-File -Encoding ascii "$Venv\Lib\site-packages\_sam2_eval_paths.pth"
# The two source-only packages are built with the setuptools already present (no isolated build environment,
# so nothing else is downloaded); both are pure Python.
uv pip install --python "$Venv\Scripts\python.exe" --no-deps --require-hashes --no-build-isolation -r "$Here\requirements-sam2-eval.txt"

# 4. Prove the application environment is unchanged.
uv pip freeze --python D:\geopoc-cache\venv-api\Scripts\python.exe | Out-File -Encoding utf8 "$Root\results\app-freeze-after-setup.txt"
if ((Get-FileHash "$Root\results\app-freeze-before.txt").Hash -ne (Get-FileHash "$Root\results\app-freeze-after-setup.txt").Hash) {
    throw 'The application environment changed. Stop and run rollback.ps1.'
}
& "$Venv\Scripts\python.exe" -c "import torch, sam2, hydra; print('torch', torch.__version__, 'from', torch.__file__); print('sam2 from', sam2.__file__)"
'Setup complete. The application environment is unchanged.'
