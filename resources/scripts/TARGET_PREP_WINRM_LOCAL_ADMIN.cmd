@echo off
setlocal EnableExtensions

echo ============================================================
echo File Distribution Studio - WinRM local-administrator mode
echo ============================================================
echo Use this only when a local Administrators member account must manage the host through WinRM.
echo.
echo IMPORTANT:
echo This script also sets LocalAccountTokenFilterPolicy=1.
echo That allows a local administrator to receive a full remote administrator token.
echo Use only in an authorized, controlled network.
echo The original value is recorded before any change.
echo Run this script as Administrator on the target Windows host.
echo.

net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] This window is not elevated.
  echo Right-click the script and choose "Run as administrator".
  pause
  exit /b 1
)

choice /C YN /N /M "Enable WinRM local-administrator mode? [Y/N]: "
if errorlevel 2 (
  echo Cancelled.
  exit /b 2
)

set "FDS_STATE_DIR=%ProgramData%\FileDistributionStudio"
set "FDS_STATE_FILE=%FDS_STATE_DIR%\LocalAccountTokenFilterPolicy.before.txt"
if not exist "%FDS_STATE_DIR%" mkdir "%FDS_STATE_DIR%" >nul 2>&1

if not exist "%FDS_STATE_FILE%" (
  reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy >nul 2>&1
  if errorlevel 1 (
    >"%FDS_STATE_FILE%" echo MISSING
  ) else (
    for /f "tokens=3" %%A in ('reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy ^| findstr /I "LocalAccountTokenFilterPolicy"') do >"%FDS_STATE_FILE%" echo %%A
  )
  echo [BACKUP] Original LocalAccountTokenFilterPolicy state saved to: %FDS_STATE_FILE%
) else (
  echo [BACKUP] Existing original-state file kept unchanged: %FDS_STATE_FILE%
)

echo.
echo [1/8] Initialize WinRM
winrm quickconfig -quiet

echo.
echo [2/8] WinRM service status
sc query WinRM

echo.
echo [3/8] Set WinRM to Automatic and start it
sc config WinRM start= auto
sc start WinRM

echo.
echo [4/8] Enable full remote token for local administrators
reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /t REG_DWORD /d 1 /f
if errorlevel 1 (
  echo [ERROR] Registry update failed. Stopping.
  pause
  exit /b 1
)

echo.
echo [5/8] Restart WinRM
sc stop WinRM
sc start WinRM

echo.
echo [6/8] Listener
winrm enumerate winrm/config/listener

echo.
echo [7/8] TCP 5985 listener
netstat -ano | findstr :5985

echo.
echo [8/8] Local WinRM identity and policy check
winrm id
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy

echo.
echo Completed.
echo Return to File Distribution Studio and test the local account with WinRM.
echo To restore the original registry state, run TARGET_RESTORE_WINRM_LOCAL_ADMIN.cmd.
pause
