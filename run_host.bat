@echo off
REM Launches the headless Claude in-game host (gtav_host.py). Pulls F10 messages, queries Claude,
REM renders replies in the in-game panel. No terminal interaction / focus stealing.
REM Resolves a Python with the deps installed, in this order:
REM   1) %GTAV_MCP_VENV%\Scripts\python.exe   (set this env var to your venv OUTSIDE Dropbox)
REM   2) ..\GTAV-Claude-MCP-venv\Scripts\python.exe   (sibling of the project, outside Dropbox)
REM   3) .venv\Scripts\python.exe   (local; AVOID inside Dropbox - it causes sync-lock corruption)
REM   4) system python on PATH
REM Setup once (recommended, OUTSIDE Dropbox):
REM   python -m venv C:\Users\%USERNAME%\GTAV-Claude-MCP-venv
REM   set GTAV_MCP_VENV=C:\Users\%USERNAME%\GTAV-Claude-MCP-venv
REM   "%GTAV_MCP_VENV%\Scripts\pip" install -r requirements.txt
cd /d "%~dp0"
set "PY="
if defined GTAV_MCP_VENV if exist "%GTAV_MCP_VENV%\Scripts\python.exe" set "PY=%GTAV_MCP_VENV%\Scripts\python.exe"
if not defined PY if exist "%~dp0..\GTAV-Claude-MCP-venv\Scripts\python.exe" set "PY=%~dp0..\GTAV-Claude-MCP-venv\Scripts\python.exe"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY (
    echo [run_host] no venv found, using system python ^(make sure deps are installed^)
    set "PY=python"
)
"%PY%" gtav_host.py
REM Only hold the window open on a real error. Clean exit (incl. single-instance guard, exit 0)
REM closes the window so Insert auto-launches don't pile up.
if errorlevel 1 pause
