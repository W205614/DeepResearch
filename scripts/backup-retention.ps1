[CmdletBinding()]
param(
  [string]$Root = '.cache/backup',
  [ValidateRange(1, 365)][int]$Keep = 7,
  [switch]$Prune
)

$ErrorActionPreference = 'Stop'
$rootPath = [System.IO.Path]::GetFullPath($Root)
New-Item -ItemType Directory -Force -Path $rootPath | Out-Null
$target = Join-Path $rootPath ('deepresearch-' + (Get-Date -Format yyyyMMdd-HHmmss))
& (Join-Path $PSScriptRoot 'backup.ps1') -Output $target
if ($LASTEXITCODE -ne 0) { throw 'Backup failed; retention pruning was not attempted.' }

if ($Prune) {
  $expired = Get-ChildItem -LiteralPath $rootPath -Directory -Filter 'deepresearch-*' |
    Sort-Object CreationTimeUtc -Descending | Select-Object -Skip $Keep
  foreach ($entry in $expired) {
    $candidate = [System.IO.Path]::GetFullPath($entry.FullName)
    if (-not $candidate.StartsWith($rootPath + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to prune backup outside root: $candidate"
    }
    Remove-Item -LiteralPath $candidate -Recurse -Force
    Write-Host "Pruned expired backup: $candidate"
  }
}
Write-Host "Backup retention completed. Kept the newest $Keep DeepResearch backups under $rootPath."
