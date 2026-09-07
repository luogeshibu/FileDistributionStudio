@echo off
setlocal
set "FAILED=0"

echo ==========================================
echo File Distribution Studio - WinRM Setup
echo ==========================================
echo.

echo [1/4] Configure WinRM...
call winrm quickconfig -quiet
if errorlevel 1 (
    echo [FAILED] WinRM quickconfig failed.
    set "FAILED=1"
) else (
    echo [OK] WinRM quickconfig completed.
)
echo.

echo [2/4] Set WinRM service startup type to Automatic...
sc config WinRM start= auto
if errorlevel 1 (
    echo [FAILED] Unable to set WinRM startup type.
    set "FAILED=1"
) else (
    echo [OK] WinRM startup type is Automatic.
)
echo.

echo [3/4] Start WinRM service...
sc start WinRM >nul 2>&1
sc query WinRM | find /I "RUNNING" >nul 2>&1
if errorlevel 1 (
    echo [FAILED] WinRM service is not running.
    set "FAILED=1"
) else (
    echo [OK] WinRM service is running.
)
echo.

echo [4/4] Set LocalAccountTokenFilterPolicy = 1...
reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /t REG_DWORD /d 1 /f
if errorlevel 1 (
    echo [FAILED] Unable to write LocalAccountTokenFilterPolicy.
    set "FAILED=1"
) else (
    reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy | find /I "0x1" >nul 2>&1
    if errorlevel 1 (
        echo [FAILED] LocalAccountTokenFilterPolicy verification failed.
        set "FAILED=1"
    ) else (
        echo [OK] LocalAccountTokenFilterPolicy = 1.
    )
)
echo.

echo ==========================================
if "%FAILED%"=="0" (
    echo SUCCESS: WinRM preparation completed.
) else (
    echo FAILED: One or more steps did not complete successfully.
    echo Please review the messages above.
)
echo ==========================================
echo.
echo This window will remain open.
pause
endlocal
