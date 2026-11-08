param([switch]$Demo, [switch]$Init, [switch]$Observability)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if ($Demo) { Write-Warning 'The -Demo switch is deprecated and no longer enables unauthenticated fixed-data mode.' }

$operation = if ($Init) { 'init' } else { 'start' }
$arguments = @('scripts/local.py', $operation)
if ($Observability) { $arguments += '--observability' }
python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Local operation failed. Follow the Python command error above.' }
