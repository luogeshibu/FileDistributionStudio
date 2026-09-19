@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "FDS_STATE_FILE=%ProgramData%\FileDistributionStudio\WinRM\ADMS\WinRM.before.txt"
set "FDS_FAILED=0"

echo ============================================================
echo File Distribution Studio - ADMS WinRM restore
echo ============================================================
echo This script runs on the TARGET Windows host only.
echo It restores the state captured by the preparation script.
echo.

powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$p=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if(-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){ exit 1 }; exit 0" >nul 2>&1
if errorlevel 1 (
    echo [FAILED] This window is not elevated.
    echo Right-click the script and choose "Run as administrator".
    goto :FAIL
)

if not exist "%FDS_STATE_FILE%" (
    echo [FAILED] Original state file was not found:
    echo %FDS_STATE_FILE%
    echo No registry or WinRM service state will be guessed or changed.
    goto :FAIL
)

set "FDS_POLICY_BEFORE="
set "FDS_START_MODE="
set "FDS_RUNNING="
for /f "tokens=1,* delims==" %%A in ('findstr /B /C:"POLICY=" "%FDS_STATE_FILE%"') do set "FDS_POLICY_BEFORE=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /B /C:"START_MODE=" "%FDS_STATE_FILE%"') do set "FDS_START_MODE=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /B /C:"RUNNING=" "%FDS_STATE_FILE%"') do set "FDS_RUNNING=%%B"

if not defined FDS_POLICY_BEFORE (
    echo [FAILED] State file is incomplete: POLICY is missing.
    goto :FAIL
)
if not defined FDS_START_MODE (
    echo [FAILED] State file is incomplete: START_MODE is missing.
    goto :FAIL
)
if not defined FDS_RUNNING (
    echo [FAILED] State file is incomplete: RUNNING is missing.
    goto :FAIL
)

echo Original policy: !FDS_POLICY_BEFORE!
echo Original WinRM startup mode: !FDS_START_MODE!
echo Original WinRM service state: !FDS_RUNNING!
choice /C YN /N /M "Restore the saved ADMS WinRM state? [Y/N]: "
if errorlevel 2 goto :CANCEL

echo [1/4] Restore LocalAccountTokenFilterPolicy...
if /I "!FDS_POLICY_BEFORE!"=="MISSING" (
    reg.exe delete HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /f >nul 2>&1
    reg.exe query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy >nul 2>&1
    if not errorlevel 1 (
        echo [FAILED] Registry value still exists after restore.
        set "FDS_FAILED=1"
    ) else (
        echo [OK] Original missing state restored.
    )
) else if /I "!FDS_POLICY_BEFORE!"=="0x0" (
    reg.exe add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /t REG_DWORD /d 0 /f >nul
    if errorlevel 1 set "FDS_FAILED=1"
) else if /I "!FDS_POLICY_BEFORE!"=="0x1" (
    reg.exe add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /t REG_DWORD /d 1 /f >nul
    if errorlevel 1 set "FDS_FAILED=1"
) else (
    echo [FAILED] Unsupported saved policy value: !FDS_POLICY_BEFORE!
    set "FDS_FAILED=1"
)

if not "%FDS_FAILED%"=="0" goto :FAIL
set "FDS_POLICY_NOW="
for /f "tokens=3" %%A in ('reg.exe query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy ^| findstr /I /C:"LocalAccountTokenFilterPolicy"') do set "FDS_POLICY_NOW=%%A"
if /I "!FDS_POLICY_BEFORE!"=="MISSING" (
    if defined FDS_POLICY_NOW (
        echo [FAILED] Policy verification failed; value is still !FDS_POLICY_NOW!.
        set "FDS_FAILED=1"
    )
) else if /I not "!FDS_POLICY_NOW!"=="!FDS_POLICY_BEFORE!" (
    echo [FAILED] Policy verification failed; current value is !FDS_POLICY_NOW!.
    set "FDS_FAILED=1"
)

echo [2/4] Restore WinRM startup mode...
if "!FDS_START_MODE!"=="2" sc.exe config WinRM start= auto >nul
if "!FDS_START_MODE!"=="3" sc.exe config WinRM start= demand >nul
if "!FDS_START_MODE!"=="4" sc.exe config WinRM start= disabled >nul
if not "!FDS_START_MODE!"=="2" if not "!FDS_START_MODE!"=="3" if not "!FDS_START_MODE!"=="4" (
    echo [FAILED] Unsupported saved WinRM startup mode: !FDS_START_MODE!
    set "FDS_FAILED=1"
)

echo [3/4] Restore WinRM service running state...
if /I "!FDS_RUNNING!"=="RUNNING" (
    sc.exe start WinRM >nul 2>&1
    sc.exe query WinRM | findstr /I /C:"RUNNING" >nul 2>&1
    if errorlevel 1 (
        echo [FAILED] WinRM was originally running but is not running now.
        set "FDS_FAILED=1"
    ) else echo [OK] WinRM is running as before.
) else if /I "!FDS_RUNNING!"=="STOPPED" (
    sc.exe stop WinRM >nul 2>&1
    timeout /t 1 /nobreak >nul 2>&1
    sc.exe query WinRM | findstr /I /C:"RUNNING" >nul 2>&1
    if not errorlevel 1 (
        echo [FAILED] WinRM was originally stopped but is still running.
        set "FDS_FAILED=1"
    ) else echo [OK] WinRM is stopped as before.
) else (
    echo [FAILED] Unsupported saved WinRM service state: !FDS_RUNNING!
    set "FDS_FAILED=1"
)

echo [4/4] Finish restore...
if not "%FDS_FAILED%"=="0" goto :FAIL
del /q "%FDS_STATE_FILE%" >nul 2>&1
if exist "%FDS_STATE_FILE%" (
    echo [WARN] Restore succeeded but the state file could not be deleted.
    echo Run this restore script only after reviewing the state file.
)
echo SUCCESS: Original ADMS WinRM state restored.
pause
endlocal
exit /b 0

:CANCEL
echo Cancelled. No changes were made.
pause
endlocal
exit /b 2

:FAIL
echo FAILED: Restore did not complete. Review the messages above.
pause
endlocal
exit /b 1
