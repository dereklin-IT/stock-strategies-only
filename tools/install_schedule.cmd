@echo off
rem ============================================================
rem  Register Windows scheduled tasks (weekdays) for this project.
rem  Double-click or run in a terminal. Re-running overwrites (/F).
rem    StockStrategies\Premarket  08:00  premarket.py
rem    StockStrategies\Daily      14:30  main.py
rem    StockStrategies\DividendAnnual  every Apr 1 18:00  dividend.py screen (annual rebalance + push)
rem  Remove:  tools\install_schedule.cmd remove
rem ============================================================
setlocal
set RUNNER=%~dp0run_job.cmd
if /i "%1"=="remove" (
  schtasks /Delete /TN "StockStrategies\Premarket" /F
  schtasks /Delete /TN "StockStrategies\Daily" /F
  schtasks /Delete /TN "StockStrategies\DividendAnnual" /F
  goto :eof
)
schtasks /Create /TN "StockStrategies\Premarket" /TR "\"%RUNNER%\" premarket" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 08:00 /F
schtasks /Create /TN "StockStrategies\Daily" /TR "\"%RUNNER%\" main" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 14:30 /F
schtasks /Create /TN "StockStrategies\DividendAnnual" /TR "\"%RUNNER%\" dividend screen" /SC MONTHLY /M APR /D 1 /ST 18:00 /F
rem Wake the PC from sleep to run; run a missed task as soon as the PC is on; run on battery too.
powershell -NoProfile -Command "foreach($n in 'Premarket','Daily','DividendAnnual'){ $t=Get-ScheduledTask -TaskPath '\StockStrategies\' -TaskName $n; $t.Settings.WakeToRun=$true; $t.Settings.StartWhenAvailable=$true; $t.Settings.DisallowStartIfOnBatteries=$false; $t.Settings.StopIfGoingOnBatteries=$false; Set-ScheduledTask -InputObject $t | Out-Null; Write-Host ('OK  ' + $n + '  WakeToRun=' + $t.Settings.WakeToRun + '  StartWhenAvailable=' + $t.Settings.StartWhenAvailable) }"
echo.
schtasks /Query /TN "StockStrategies\Premarket"
schtasks /Query /TN "StockStrategies\Daily"
schtasks /Query /TN "StockStrategies\DividendAnnual"
endlocal
