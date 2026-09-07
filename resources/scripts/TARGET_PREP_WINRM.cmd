@echo off
setlocal EnableExtensions

echo ============================================================
echo File Distribution Studio - Standard WinRM preparation
echo ============================================================
echo This script initializes and starts WinRM only.
echo It does NOT modify UAC, registry policy, TrustedHosts, or user permissions.
echo Run this script as Administrator on the target Windows host.
echo.

net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] This window is not elevated.
  echo Right-click the script and choose "Run as administrator".
  pause
  exit /b 1
)

echo [1/6] Initialize WinRM
winrm quickconfig -quiet

echo.
echo [2/6] WinRM service status
sc query WinRM

echo.
echo [3/6] Set WinRM to Automatic and start it
sc config WinRM start= auto
sc start WinRM

echo.
echo [4/6] Listener
winrm enumerate winrm/config/listener

echo.
echo [5/6] TCP 5985 listener
netstat -ano | findstr :5985

echo.
echo [6/6] Local WinRM identity
winrm id

echo.
echo Completed. Return to File Distribution Studio and click "Test WinRM".
pause
