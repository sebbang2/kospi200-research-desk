param(
  [string]$UniverseCsv = "",
  [switch]$RunNow
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($UniverseCsv)) {
  $UniverseCsv = Join-Path $root 'kospi200_universe.csv'
}
if (-not (Test-Path -LiteralPath $UniverseCsv)) {
  throw "Universe CSV not found: $UniverseCsv. Run bootstrap_kospi200.py first."
}

$taskScript = Join-Path $root 'run_refresh.ps1'
$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$taskScript`" -UniverseCsv `"$UniverseCsv`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $root
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$triggerNoon = New-ScheduledTaskTrigger -Daily -At "12:00"
$trigger1540 = New-ScheduledTaskTrigger -Daily -At "15:40"
Register-ScheduledTask -TaskName "KOSPI200-Research-12-00" -Action $action -Trigger $triggerNoon -Principal $principal -Settings $settings -Description "Refresh KOSPI200 public valuation and keyword news at 12:00 KST." -Force | Out-Null
Register-ScheduledTask -TaskName "KOSPI200-Research-15-40" -Action $action -Trigger $trigger1540 -Principal $principal -Settings $settings -Description "Refresh KOSPI200 public valuation and keyword news at 15:40 KST." -Force | Out-Null

Write-Host "Scheduled tasks installed for 12:00 and 15:40 local time."
if ($RunNow) {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $taskScript -UniverseCsv $UniverseCsv
}
