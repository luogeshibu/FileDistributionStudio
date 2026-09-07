@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call setup.bat --ensure
if errorlevel 1 (
  pause
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\build.ps1"
if errorlevel 1 (
  echo 打包失败。
  pause
  exit /b 1
)
echo 打包完成。
pause
