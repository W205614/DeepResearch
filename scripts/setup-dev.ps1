$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path (Get-Location) '.cache\python'
$env:UV_PYTHON_BIN_DIR = Join-Path (Get-Location) '.cache\bin'
uv python install 3.11 --no-registry
if ($LASTEXITCODE -ne 0) { throw 'Project Python setup failed' }
uv venv --python 3.11 .venv --allow-existing
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
uv sync --frozen --group dev --no-python-downloads
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
Write-Host 'Use .venv\Scripts\python.exe. No global project dependencies were installed.'
