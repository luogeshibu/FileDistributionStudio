@echo off
setlocal
set "FAILED=0"

echo ==========================================
echo File Distribution Studio - WinRM Restore
echo ==========================================
echo.

echo [1/2] Remove LocalAccountTokenFilterPolicy...
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy >nul 2>&1
if errorlevel 1 (
    echo [OK] LocalAccountTokenFilterPolicy does not exist. No change needed.
) else (
    reg delete HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /f
    if errorlevel 1 (
        echo [FAILED] Unable to remove LocalAccountTokenFilterPolicy.
        set "FAILED=1"
    ) else (
        echo [OK] LocalAccountTokenFilterPolicy removed.
    )
)
echo.

echo [2/2] Stop WinRM service...
sc stop WinRM >nul 2>&1
timeout /t 1 /nobreak >nul 2>&1
sc query WinRM | find /I "RUNNING" >nul 2>&1
if errorlevel 1 (
    echo [OK] WinRM service is stopped.
) else (
    echo [FAILED] WinRM service is still running.
    set "FAILED=1"
)
echo.

echo ==========================================
if "%FAILED%"=="0" (
    echo SUCCESS: WinRM restore completed.
) else (
    echo FAILED: One or more steps did not complete successfully.
    echo Please review the messages above.
)
echo ==========================================
echo.
echo This window will remain open.
pause
endlocal
