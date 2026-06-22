@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM  GTAV-Claude-MCP  -  one-click in-game bridge installer
REM  Copies ClaudeBridge.dll (+ deps) into your GTA V scripts\ folder.
REM  Run this from the folder that contains the DLLs (the release zip / dist\).
REM ============================================================================
echo.
echo   GTAV-Claude-MCP  bridge installer
echo   ---------------------------------
echo.

set "SRC=%~dp0"
set "GTA="

REM --- Try to find a running GTA first (most reliable) ---
for /f "tokens=2 delims=," %%P in ('tasklist /fi "imagename eq GTA5.exe" /fo csv /nh 2^>nul') do set "RUNNING=1"

REM --- Common install locations (Legacy + Enhanced, Rockstar + Steam + Epic) ---
set "C1=C:\Program Files\Rockstar Games\Grand Theft Auto V Legacy"
set "C2=C:\Program Files\Rockstar Games\Grand Theft Auto V Enhanced"
set "C3=C:\Program Files\Rockstar Games\Grand Theft Auto V"
set "C4=C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto V"
set "C5=C:\Program Files\Epic Games\GTAV"
for %%D in ("%C1%" "%C2%" "%C3%" "%C4%" "%C5%") do (
    if exist "%%~D\GTA5.exe" set "GTA=%%~D"
    if exist "%%~D\GTA5_Enhanced.exe" set "GTA=%%~D"
)

if not defined GTA (
    echo   Could not auto-find your GTA V folder.
    set /p GTA=  Paste the full path to your GTA V folder (the one with GTA5.exe):
)
if not exist "%GTA%\GTA5.exe" if not exist "%GTA%\GTA5_Enhanced.exe" (
    echo   [X] "%GTA%" doesn't contain GTA5.exe - aborting.
    pause & exit /b 1
)
echo   Game folder: %GTA%

REM --- Warn if ScriptHookV / SHVDN aren't present ---
if not exist "%GTA%\ScriptHookV.dll"        echo   [!] ScriptHookV.dll not found - install ScriptHookV first.
if not exist "%GTA%\ScriptHookVDotNet.asi"  echo   [!] ScriptHookVDotNet not found - install the SHVDN 3 NIGHTLY first.

REM --- Make the scripts folder if needed ---
if not exist "%GTA%\scripts" (
    echo   Creating scripts\ folder...
    mkdir "%GTA%\scripts"
)

REM --- Copy the bridge + deps ---
set "OK=1"
for %%F in (ClaudeBridge.dll 0Harmony.dll LemonUI.SHVDN3.dll) do (
    if exist "%SRC%%%F" (
        copy /y "%SRC%%%F" "%GTA%\scripts\" >nul && echo   + %%F
    ) else (
        echo   [!] %%F not found next to this installer - skipped.
        if "%%F"=="ClaudeBridge.dll" set "OK="
    )
)
if not defined OK ( echo   [X] ClaudeBridge.dll missing - put this .bat next to the DLLs. & pause & exit /b 1 )

echo.
echo   DONE. The bridge is installed.
echo   1) Launch GTA V in story mode (offline). It loads automatically.
echo   2) Press F11 in-game to open the Claude panel, F10 to chat.
echo   3) For autonomous Claude replies, run the host (see README / run_host.bat).
echo.
pause
