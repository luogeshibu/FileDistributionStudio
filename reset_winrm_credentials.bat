@echo off
setlocal
echo Removing the saved File Distribution Studio WinRM default credential...
cmdkey.exe /delete:FileDistributionStudio:WinRM:Default >nul 2>&1
if errorlevel 1 (
  echo No saved default credential was found.
) else (
  echo Saved default WinRM credential removed.
)
echo RDP and other Windows credentials were not changed.
exit /b 0
