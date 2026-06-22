@echo off
REM ============================================================================
REM  Build ClaudeHost.exe - a self-contained host so players need NO Python.
REM  Run this ONCE (you need the venv from SETUP.md). Produces dist\ClaudeHost.exe.
REM
REM  The .exe still requires the `claude` CLI on the system and `claude /login`
REM  done once (the Agent SDK drives it with your Claude subscription - no API key).
REM ============================================================================
setlocal
cd /d "%~dp0"

REM Resolve the venv python (set by SETUP.md's setx, else fall back).
set "VPY=%GTAV_MCP_PYTHON%"
if not defined VPY set "VPY=%USERPROFILE%\GTAV-Claude-MCP-venv\Scripts\python.exe"
if not exist "%VPY%" (
    echo [X] venv python not found at "%VPY%".
    echo     Create it per SETUP.md, or set GTAV_MCP_PYTHON, then re-run.
    pause & exit /b 1
)
echo Using python: %VPY%

echo Installing PyInstaller...
"%VPY%" -m pip install --upgrade pyinstaller >nul

echo Building ClaudeHost.exe (one file, ~30-60s)...
"%VPY%" -m PyInstaller --onefile --name ClaudeHost ^
    --collect-all claude_agent_sdk ^
    --collect-all mcp ^
    --hidden-import anyio ^
    --console ^
    gtav_host.py

if exist "dist\ClaudeHost.exe" (
    echo.
    echo  DONE -^> dist\ClaudeHost.exe
    echo  Double-click it (with GTA running) to start the in-game Claude host.
    echo  Requires: the `claude` CLI installed + `claude /login` done once.
) else (
    echo  [X] build failed - see PyInstaller output above.
)
echo.
pause
