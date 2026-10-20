param([switch]$Demo)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env. Configure your API keys there for live research.'
}
docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop first.' }
if ($Demo) { Write-Warning 'The -Demo switch is deprecated and no longer enables unauthenticated fixed-data mode.' }

docker compose up -d --build --wait --wait-timeout 300
if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Inspect: docker compose ps / docker compose logs --tail 80' }
$appPort = '8080'
$portLine = Get-Content -LiteralPath '.env' | Where-Object { $_ -match '^APP_PORT=' } | Select-Object -First 1
if ($portLine) { $appPort = ($portLine -split '=', 2)[1].Trim() }
Write-Host "DeepResearch is running at http://localhost:$appPort (persistent APP_PORT in .env)."
