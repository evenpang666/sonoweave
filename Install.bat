@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Please install 64-bit Python 3.11 or 3.12, and add Python to PATH.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" goto failed
".venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Installation complete. Run PrepareModels.bat once, then Start.bat.
pause
exit /b 0
:failed
echo Installation failed. Check Python and your network connection.
pause
exit /b 1
