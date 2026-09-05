param([switch]$Demo)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env. Configure your API keys there for live research.'
}
docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop first.' }
if ($Demo) {
    docker compose -p deepresearch-demo -f compose.yaml -f compose.demo.yaml up -d --build --wait --wait-timeout 300
} else {
    docker compose up -d --build --wait --wait-timeout 300
}
if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Inspect: docker compose ps / docker compose logs --tail 80' }
Write-Host 'DeepResearch is running. Default address: http://localhost:8080 (APP_PORT in .env).'
