[CmdletBinding()]
param([switch]$Start)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
$arguments = @((Join-Path $PSScriptRoot 'smoke-enterprise.py'))
if ($Start) { $arguments += '--start' }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Enterprise smoke failed.' }
