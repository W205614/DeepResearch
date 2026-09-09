[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
docker compose stop milvus
try {
  Start-Sleep -Seconds 3
  & docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5)"
  if ($LASTEXITCODE -eq 0) { throw 'ready endpoint unexpectedly succeeded during Milvus outage' }
} finally { docker compose up -d milvus; docker compose up -d backend worker; docker compose up -d --wait --wait-timeout 300 }
& "$PSScriptRoot\smoke-enterprise.ps1"