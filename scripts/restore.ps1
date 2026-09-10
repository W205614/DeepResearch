[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][Alias('Input')][string]$BackupPath,
  [string]$Project = ('dr-restore-' + (Get-Date -Format yyyyMMddHHmmss)),
  [switch]$ReplaceVolumes
)
$ErrorActionPreference = 'Stop'
if ($ReplaceVolumes) { throw 'In-place volume replacement is disabled. Use a fresh -Project dr-restore-... instead.' }
$repoPath = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $repoPath '.venv/Scripts/python.exe'
& $pythonPath (Join-Path $PSScriptRoot 'recovery.py') restore --path ([System.IO.Path]::GetFullPath($BackupPath)) --project $Project
if ($LASTEXITCODE -ne 0) { throw 'Recovery verification failed.' }
