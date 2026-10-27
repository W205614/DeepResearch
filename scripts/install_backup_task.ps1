[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Configuration,
    [string]$DailyAt = '03:30',
    [string]$TaskName = 'DeepResearch-Verified-Cold-Backup',
    [string]$ProbeTaskName = 'DeepResearch-Host-Probe',
    [ValidateRange(1,60)][int]$ProbeEveryMinutes = 5
)
$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$configurationPath = (Resolve-Path -LiteralPath $Configuration).Path
$backupConfiguration = Get-Content -LiteralPath $configurationPath -Raw | ConvertFrom-Json
if ($backupConfiguration.enabled -ne $true -or $backupConfiguration.maintenance_window_acknowledged -ne $true) {
    throw 'Explicit enabled and maintenance_window_acknowledged are required.'
}
if ([TimeZoneInfo]::Local.GetUtcOffset([DateTime]::Now).TotalHours -ne 8) {
    throw 'This installer requires a UTC+08 host for the declared Asia/Shanghai daily time. Convert and verify the native scheduler trigger before installation on another host timezone.'
}
$pythonExecutable = Join-Path $workspacePath '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Windowless workspace Python is required; no console fallback is permitted.' }
if (-not (Test-Path -LiteralPath (Join-Path $workspacePath '.venv\Scripts\python.exe'))) { throw 'Workspace Python for the hidden logged child is unavailable.' }
$backupScript = Join-Path $PSScriptRoot 'scheduled_backup.py'
function Assert-OwnedTask([string]$Name, [string]$ScriptPath) {
    $existingTask = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -ne $existingTask) {
        foreach ($existingAction in $existingTask.Actions) {
            if ($existingAction.Arguments -notlike ('*' + $ScriptPath + '*')) {
                throw ('Existing task is not owned by this entry point: ' + $Name)
            }
        }
    }
}
Assert-OwnedTask $TaskName $backupScript
$probeScript = Join-Path $PSScriptRoot 'ops_probe.py'
Assert-OwnedTask $ProbeTaskName $probeScript
$arguments = '"' + $backupScript + '" --config "' + $configurationPath + '"'
$action = New-ScheduledTaskAction -Execute $pythonExecutable -Argument $arguments -WorkingDirectory $workspacePath
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable
# Interactive user context deliberately avoids storing a Windows password.
# Docker Desktop must be running in this user's session when the job fires.
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Verified DeepResearch cold backup; private configuration controls maintenance window and optional off-host copy.' -Force | Out-Null
$probeArguments = '"' + $probeScript + '" --config "' + $configurationPath + '" --capacity-only'
$probeAction = New-ScheduledTaskAction -Execute $pythonExecutable -Argument $probeArguments -WorkingDirectory $workspacePath
$probeTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $ProbeEveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$probeSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -StartWhenAvailable
Register-ScheduledTask -TaskName $ProbeTaskName -Action $probeAction -Trigger $probeTrigger -Settings $probeSettings -Principal $principal -Description 'Read-only host capacity and backup-age sample. Preserves last complete checksum verification; never stops Docker or sends a notification.' -Force | Out-Null
Write-Output ('Installed tasks ' + $TaskName + ' and ' + $ProbeTaskName + '; no backup was executed during installation. The first capacity sample cannot create a verified-backup timestamp.')
