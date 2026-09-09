[CmdletBinding()]
param(
  [string]$Output = (".cache/backup/deepresearch-" + (Get-Date -Format yyyyMMdd-HHmmss))
)

$ErrorActionPreference = 'Stop'
$compose = @()
$volumeNames = @('research-data', 'milvus-data', 'etcd-data', 'minio-data', 'redis-data', 'keycloak-data', 'grafana-data')
$destination = [System.IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Force -Path $destination | Out-Null

function Invoke-Compose {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
  & docker compose @compose @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Docker Compose command failed: $($Arguments -join ' ')" }
}

$databasePath = Join-Path $destination 'postgres.sql'
Invoke-Compose exec -T postgres pg_dump --username deepresearch --format=plain deepresearch | Out-File -FilePath $databasePath -Encoding utf8
foreach ($name in $volumeNames) {
  $volume = "deepresearch_$name"
  $archive = "$name.tgz"
  & docker volume inspect $volume *> $null
  if ($LASTEXITCODE -ne 0) { Write-Warning "Volume $volume does not exist; skipped."; continue }
  & docker run --rm -v "${volume}:/source:ro" -v "${destination}:/backup" alpine:3.20 tar -C /source -czf "/backup/$archive" .
  if ($LASTEXITCODE -ne 0) { throw "Could not archive volume $volume" }
}
$files = Get-ChildItem -File $destination | ForEach-Object { [ordered]@{ name = $_.Name; sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash } }
@{ created_at = (Get-Date).ToString('o'); compose_project = 'deepresearch'; files = $files } |
  ConvertTo-Json -Depth 4 | Set-Content (Join-Path $destination 'manifest.json') -Encoding utf8
Write-Host "Backup written to $destination. Restore only into a stopped demonstration environment." -ForegroundColor Green
