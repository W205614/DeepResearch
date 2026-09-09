[CmdletBinding()]
param(
  [string]$TaskName = 'DeepResearchDailyBackup',
  [string]$BackupRoot = '.cache/backup',
  [string]$At = '02:00'
)

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'backup-retention.ps1'
$rootPath = [System.IO.Path]::GetFullPath($BackupRoot)
$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Root `"$rootPath`" -Keep 7 -Prune"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At $At
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description 'DeepResearch local backup with seven-copy retention' -Force | Out-Null
Write-Host "Installed scheduled task $TaskName at $At. It keeps seven local backups under $rootPath."
