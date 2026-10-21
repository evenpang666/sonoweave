@echo off
setlocal
cd /d "%~dp0"
if exist "dist\SonoWeave\SonoWeave.exe" (
    start "" "dist\SonoWeave\SonoWeave.exe"
    exit /b
)
if not exist ".venv\Scripts\pythonw.exe" (
    echo Please run Install.bat first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "main.py"
