@echo off
REM ExtendedLSC performance capture launcher.
REM Runs the PowerShell capture script (bypassing the execution-policy prompt) and keeps the window open.
cd /d "%~dp0"
echo.
echo  ExtendedLSC performance capture
echo  --------------------------------
echo  Make sure GTA V is running and you are IN the situation that drops your FPS
echo  (driving / menu open / wherever it lags). The capture takes about 40 seconds -
echo  keep playing in that laggy state the whole time.
echo.
pause
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Capture-ELSCPerf.ps1"
echo.
echo  Done. Send ELSC_perf_report.txt (in this folder) to the mod author.
echo.
pause
