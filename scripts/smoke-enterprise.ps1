[CmdletBinding()]
param(
  [switch]$Start
)

$ErrorActionPreference = 'Stop'
$compose = @('-f', 'compose.yaml', '-f', 'compose.enterprise.yaml')

function Invoke-Compose {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
  & docker compose @compose @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Docker Compose command failed: $($Arguments -join ' ')" }
}

function Assert-Http {
  param([string]$Uri, [string]$ExpectedIssuer = '')
  $lastError = $null
  for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
      $response = Invoke-RestMethod -Uri $Uri -TimeoutSec 5
      if ($ExpectedIssuer -and $response.issuer -ne $ExpectedIssuer) {
        throw "unexpected issuer: $($response.issuer)"
      }
      return
    } catch { $lastError = $_; Start-Sleep -Seconds 2 }
  }
  throw "Timed out waiting for $Uri. Last error: $lastError"
}

if ($Start) { Invoke-Compose up -d --build --wait --wait-timeout 300 }

Assert-Http 'http://127.0.0.1:8080/healthz'
Assert-Http 'http://127.0.0.1:8180/realms/deepresearch/.well-known/openid-configuration' 'http://localhost:8180/realms/deepresearch'
$grafana = Invoke-RestMethod -Uri 'http://127.0.0.1:3000/api/health' -TimeoutSec 10
if ($grafana.database -ne 'ok') { throw 'Grafana is not ready.' }
Invoke-Compose exec -T postgres psql -U deepresearch -d deepresearch -tAc 'SELECT version_num FROM alembic_version' | Select-String -Quiet '^0001_enterprise_workspace$' | ForEach-Object {
  if (-not $_) { throw 'Alembic migration version is not 0001_enterprise_workspace.' }
}
Invoke-Compose exec -T redis redis-cli ping | Select-String -Quiet '^PONG$' | ForEach-Object {
  if (-not $_) { throw 'Redis did not return PONG.' }
}
Invoke-Compose exec -T backend python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5).status == 200"
Invoke-Compose exec -T backend python -c "import socket; socket.create_connection(('otel-collector', 4317), timeout=5).close()"
Invoke-Compose ps worker | Select-String -Quiet 'healthy' | ForEach-Object {
  if (-not $_) { throw 'Worker is not healthy.' }
}
Write-Host 'Enterprise smoke passed: web, OIDC discovery, migration, Redis, backend readiness, worker, Grafana, and OpenTelemetry collector.' -ForegroundColor Green