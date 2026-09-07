@echo off
setlocal EnableExtensions

echo ============================================================
echo File Distribution Studio - Restore local-admin remote-token policy
echo ============================================================
echo This script restores LocalAccountTokenFilterPolicy to the state recorded before setup.
echo It does not stop WinRM or modify other WinRM settings.
echo.

net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] This window is not elevated.
  echo Right-click the script and choose "Run as administrator".
  pause
  exit /b 1
)

set "FDS_STATE_FILE=%ProgramData%\FileDistributionStudio\LocalAccountTokenFilterPolicy.before.txt"
if not exist "%FDS_STATE_FILE%" (
  echo [ERROR] Original-state file was not found:
  echo %FDS_STATE_FILE%
  echo No registry value will be guessed or changed.
  pause
  exit /b 1
)

set /p FDS_ORIGINAL=<"%FDS_STATE_FILE%"
echo Original state: %FDS_ORIGINAL%
choice /C YN /N /M "Restore this state? [Y/N]: "
if errorlevel 2 exit /b 2

if /I "%FDS_ORIGINAL%"=="MISSING" (
  reg delete HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /f
) else (
  reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /t REG_DWORD /d %FDS_ORIGINAL% /f
)

if errorlevel 1 (
  echo [ERROR] Restore failed.
  pause
  exit /b 1
)

echo Restore completed.
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy
pause
