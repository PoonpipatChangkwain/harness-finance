@echo off
rem Hourly runner for forward_ab.py (registered in Task Scheduler as "XAU forward AB").
rem Remove with:  schtasks /Delete /TN "XAU forward AB" /F
set PY=%USERPROFILE%\.venvs\xau-ml\Scripts\python.exe
set HERE=%~dp0
if not exist "%HERE%results\forward_ab" mkdir "%HERE%results\forward_ab"
echo ===== %date% %time% ===== >> "%HERE%results\forward_ab\run.log"
"%PY%" "%HERE%forward_ab.py" >> "%HERE%results\forward_ab\run.log" 2>&1
