$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backendRoot = Join-Path $projectRoot 'backend'
$pythonWindowless = Join-Path $backendRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Backend Python environment is missing' }
# Reuse the existing application's interactive, limited-privilege identity.
$template = Get-ScheduledTask -TaskName 'zucai-jingcai-odds'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$cycleTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
$seasonTrigger = New-ScheduledTaskTrigger -Daily -At '08:30'
foreach ($spec in @(
    @{ Name='zucai-reference-cycle'; Script='run_reference_cycle.py'; Trigger=$cycleTrigger },
    @{ Name='zucai-reference-fixtures'; Script='sync_reference_fixtures.py'; Trigger=$seasonTrigger }
)) {
    $scriptPath = Join-Path $backendRoot ('scripts\' + $spec.Script)
    $action = New-ScheduledTaskAction -Execute $pythonWindowless -Argument ('-X utf8 "' + $scriptPath + '"') -WorkingDirectory $backendRoot
    Register-ScheduledTask -TaskName $spec.Name -Action $action -Trigger $spec.Trigger -Principal $template.Principal -Settings $settings -Description 'Football reference data: observed prematch archives and regulation-time settlement. Never backfill missed prediction windows.' -Force | Select-Object TaskName, State
}
