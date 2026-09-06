param(
  [Parameter(Mandatory = $true)][string]$Input
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Input)) { throw "Backup file does not exist: $Input" }
Get-Content -Encoding Byte -Path $Input | docker compose exec -T postgres pg_restore --clean --if-exists --no-owner --username deepresearch --dbname deepresearch
Write-Host "PostgreSQL restore completed. Verify /readyz before accepting traffic."
