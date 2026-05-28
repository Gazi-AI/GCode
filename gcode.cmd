@echo off
setlocal

set "GCODE_ROOT=%~dp0"
cd /d "%GCODE_ROOT%"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" gcode_launcher.py %*
) else (
    python gcode_launcher.py %*
)
