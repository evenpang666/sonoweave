@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Please run Install.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "tools\prepare_models.py"
if errorlevel 1 (
    echo Model preparation failed. Check your network, then run this again.
    pause
    exit /b 1
)
echo Models ready. Double-click Start.bat to launch offline.
pause
