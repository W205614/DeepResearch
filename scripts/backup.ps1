param(
  [string]$Output = ".cache/backup/deepresearch-$(Get-Date -Format yyyyMMdd-HHmmss).dump"
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Split-Path $Output -Parent) | Out-Null
docker compose exec -T postgres pg_dump --format=custom --username deepresearch deepresearch | Set-Content -Encoding Byte -Path $Output
Write-Host "PostgreSQL backup written to $Output"
