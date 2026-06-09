@echo off
REM Launches the headless Claude in-game host. Pulls your in-game F10 messages, queries Claude,
REM renders replies in the in-game panel. No terminal interaction / focus stealing.
REM Uses a local .venv if present, else system python.
REM   Setup once:  python -m venv .venv  &&  .venv\Scripts\pip install -r requirements.txt
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" gtav_host.py
) else (
    echo [run_host] no .venv found, using system python
    python gtav_host.py
)
pause
