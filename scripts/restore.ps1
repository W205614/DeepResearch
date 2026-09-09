[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Input,
  [switch]$ReplaceVolumes
)

$ErrorActionPreference = 'Stop'
if (-not $ReplaceVolumes) { throw 'Restoration replaces local named volumes. Re-run with -ReplaceVolumes after stopping the demo environment.' }
$source = [System.IO.Path]::GetFullPath($Input)
if (-not (Test-Path (Join-Path $source 'manifest.json'))) { throw 'Backup manifest does not exist.' }
$manifest = Get-Content -Raw (Join-Path $source 'manifest.json') | ConvertFrom-Json
foreach ($file in $manifest.files) {
  $path = Join-Path $source $file.name
  if (-not (Test-Path $path) -or (Get-FileHash $path -Algorithm SHA256).Hash -ne $file.sha256) { throw "Backup checksum failed: $($file.name)" }
}
$compose = @()
$volumeNames = @('milvus-data', 'etcd-data', 'minio-data', 'redis-data', 'keycloak-data', 'grafana-data')

function Invoke-Compose {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
  & docker compose @compose @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Docker Compose command failed: $($Arguments -join ' ')" }
}

Invoke-Compose down
foreach ($name in $volumeNames) {
  $archive = Join-Path $source "$name.tgz"
  if (-not (Test-Path $archive)) { continue }
  $volume = "deepresearch_$name"
  & docker volume rm $volume *> $null
  & docker volume create $volume *> $null
  & docker run --rm -v "${volume}:/target" -v "${source}:/backup:ro" alpine:3.20 tar -C /target -xzf "/backup/$name.tgz"
  if ($LASTEXITCODE -ne 0) { throw "Could not restore volume $volume" }
}
Invoke-Compose up -d postgres
for ($attempt = 0; $attempt -lt 30; $attempt++) {
  & docker compose @compose exec -T postgres pg_isready -U deepresearch -d deepresearch *> $null
  if ($LASTEXITCODE -eq 0) { break }
  Start-Sleep -Seconds 2
}
Get-Content -Raw (Join-Path $source 'postgres.sql') | docker compose @compose exec -T postgres psql --username deepresearch --dbname deepresearch
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL restore failed.' }
Write-Host 'Restore completed. Start the remaining Compose services and run scripts/smoke-enterprise.ps1 before accepting traffic.' -ForegroundColor Green
