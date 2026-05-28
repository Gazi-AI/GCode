@echo off
setlocal

set "GCODE_ROOT=%~dp0"
cd /d "%GCODE_ROOT%"

echo Opening the graphical GCode setup wizard...
echo If the window does not appear, keep this console open and read the error below.
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "%GCODE_ROOT%setup_wizard.py" --install --force
) else (
    python "%GCODE_ROOT%setup_wizard.py" --install --force
)

if errorlevel 1 (
    echo.
    echo Setup wizard failed. You can also run:
    echo python "%GCODE_ROOT%setup_wizard.py" --install --force
    echo.
    pause
)
