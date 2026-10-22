@echo off
setlocal
cd /d "%~dp0"
call Install.bat --ensure
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" "tools\prepare_models.py" %*
if errorlevel 1 (
    echo Model preparation failed. Check your network, then run this again.
    pause
    exit /b 1
)
echo Models ready. Double-click Start.bat to launch offline.
pause
