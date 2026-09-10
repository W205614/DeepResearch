[CmdletBinding()]
param([string]$Output = ('.cache/backup/deepresearch-' + (Get-Date -Format yyyyMMdd-HHmmss)))
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $repoPath '.venv/Scripts/python.exe'
& $pythonPath (Join-Path $PSScriptRoot 'recovery.py') backup --path ([System.IO.Path]::GetFullPath($Output))
if ($LASTEXITCODE -ne 0) { throw 'Consistent backup failed; do not use this backup directory.' }
