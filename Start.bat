@echo off
setlocal
cd /d "%~dp0"
rem A checkout must run current source, even when an older build is present.
if not exist "main.py" goto bundle
call Install.bat --ensure
if errorlevel 1 (
    echo Environment setup failed. The app was not started.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "main.py" %*
exit /b
:bundle
if exist "dist\SonoWeave\SonoWeave.exe" (
    start "" "dist\SonoWeave\SonoWeave.exe" %*
    exit /b
)
echo Cannot find main.py or dist\SonoWeave\SonoWeave.exe.
exit /b 1
