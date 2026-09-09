[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
docker compose stop worker
if ($LASTEXITCODE -ne 0) { throw 'Could not stop worker for restart drill.' }
docker compose up -d --wait worker
if ($LASTEXITCODE -ne 0) { throw 'Worker did not become ready after restart.' }
& (Join-Path $PSScriptRoot 'smoke-enterprise.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Enterprise smoke failed after worker restart.' }
Write-Host 'Worker restart drill passed. Persisted task recovery remains covered by the runtime regression suite.'
