@echo off
setlocal
cd /d "%~dp0"
if exist "dist\SonoWeave\SonoWeave.exe" (
    start "" "dist\SonoWeave\SonoWeave.exe" %*
    exit /b
)
call Install.bat --ensure
if errorlevel 1 (
    echo Environment setup failed. The app was not started.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "main.py" %*
