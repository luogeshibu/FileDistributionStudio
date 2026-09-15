@echo off
setlocal
cd /d "%~dp0"
call "%~dp0reset_winrm_credentials.bat"
echo This removes only the local Python virtual environment (.venv).
choice /C YN /M "Continue"
if errorlevel 2 exit /b 0
if exist ".venv" rmdir /S /Q ".venv"
call setup.bat
pause
