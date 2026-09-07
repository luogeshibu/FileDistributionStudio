@echo off
setlocal
cd /d "%~dp0"

call setup.bat --ensure
if errorlevel 1 (
  echo.
  echo File Distribution Studio could not prepare its Python environment.
  echo Fix the error shown above and run run.bat again.
  pause
  exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" main.py
