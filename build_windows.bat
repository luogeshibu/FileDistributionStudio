@echo off
setlocal
cd /d "%~dp0"
call setup.bat --ensure
if errorlevel 1 (
  pause
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\build.ps1"
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo Build completed.
pause
