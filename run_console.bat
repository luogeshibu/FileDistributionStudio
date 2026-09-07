@echo off
setlocal
cd /d "%~dp0"
call setup.bat --ensure
if errorlevel 1 (
  pause
  exit /b 1
)
".venv\Scripts\python.exe" main.py
pause
