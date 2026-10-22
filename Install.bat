@echo off
setlocal
cd /d "%~dp0"
set "OPTIONS=-Backend vulkan"
if "%~1"=="--cpu" set "OPTIONS=-Backend cpu"
if "%~1"=="--ensure" set "OPTIONS=-Backend auto -Ensure"
if not "%~2"=="" goto usage
if not "%~1"=="" if not "%~1"=="--cpu" if not "%~1"=="--ensure" goto usage
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "tools\bootstrap.ps1" %OPTIONS%
if errorlevel 1 exit /b 1
exit /b 0
:usage
echo Usage: Install.bat [--cpu]
exit /b 2
