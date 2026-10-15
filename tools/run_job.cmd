@echo off
rem ============================================================
rem  Run one job of stock-strategies locally and append to a log.
rem  Usage:  tools\run_job.cmd main        (daily signal, 14:50)
rem          tools\run_job.cmd premarket   (night session, 08:00)
rem          tools\run_job.cmd dividend screen   (dividend screen)
rem  Logs:   logs\<job>_<yyyy-mm-dd>.log  (UTF-8)
rem ============================================================
setlocal
if "%~1"=="" (
  echo Usage: tools\run_job.cmd main ^| premarket ^| dividend screen
  exit /b 1
)
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
set LOG=logs\%1_%TODAY%.log
echo ===== START %DATE% %TIME%  job=%* ===== >> "%LOG%"
uv run python %1.py %2 %3 %4 >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo ===== END   %DATE% %TIME%  exit=%RC% ===== >> "%LOG%"
endlocal & exit /b %RC%
