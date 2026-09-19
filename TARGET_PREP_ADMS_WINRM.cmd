@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "FDS_FAILED=0"
set "FDS_STATE_DIR=%ProgramData%\FileDistributionStudio\WinRM\ADMS"
set "FDS_STATE_FILE=%FDS_STATE_DIR%\WinRM.before.txt"
set "FDS_LISTENER_FILE=%TEMP%\FDS_WinRM_listener_%RANDOM%.txt"
set "FDS_AUTH_FILE=%TEMP%\FDS_WinRM_auth_%RANDOM%.txt"

echo ============================================================
echo File Distribution Studio - ADMS WinRM target preparation
echo ============================================================
echo This script runs on the TARGET Windows host only.
echo The management/client machine does not need WinRM setup.
echo ADMS must already exist, be enabled, and belong to local Administrators.
echo The script does not create accounts or change group membership.
echo.

powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$p=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if(-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){ exit 1 }; exit 0" >nul 2>&1
if errorlevel 1 (
    echo [FAILED] This window is not elevated.
    echo Right-click the script and choose "Run as administrator".
    goto :FAIL
)

echo [1/8] Verify local ADMS account and Administrators membership...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $u=Get-CimInstance Win32_UserAccount | Where-Object { $_.LocalAccount -and $_.Name -ieq 'ADMS' } | Select-Object -First 1; if($null -eq $u){ Write-Error 'Local ADMS account was not found.'; exit 2 }; if([bool]$u.Disabled){ Write-Error 'Local ADMS account is disabled.'; exit 3 }; $sid=New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544'); $groupName=$sid.Translate([System.Security.Principal.NTAccount]).Value.Split([char]92)[-1]; $group=[ADSI]('WinNT://./'+$groupName+',group'); $members=@($group.psbase.Invoke('Members')) | ForEach-Object { $_.GetType().InvokeMember('Name','GetProperty',$null,$_,$null) }; if(-not (@($members | Where-Object { [string]$_ -ieq 'ADMS' }))){ Write-Error ('ADMS is not a member of local Administrators: '+$groupName); exit 4 }; Write-Host '[OK] ADMS exists, is enabled, and is a local administrator.'; exit 0"
if errorlevel 1 goto :FAIL

echo [2/8] Back up original WinRM and Remote UAC state...
if not exist "%FDS_STATE_DIR%\" mkdir "%FDS_STATE_DIR%" >nul 2>&1
if errorlevel 1 (
    echo [FAILED] Could not create state directory: %FDS_STATE_DIR%
    goto :FAIL
)

sc.exe query WinRM >nul 2>&1
if errorlevel 1 (
    echo [FAILED] WinRM service was not found.
    goto :FAIL
)

if not exist "%FDS_STATE_FILE%" (
    set "FDS_POLICY_BEFORE=MISSING"
    reg.exe query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy >nul 2>&1
    if not errorlevel 1 (
        set "FDS_POLICY_BEFORE="
        for /f "tokens=3" %%A in ('reg.exe query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy ^| findstr /I /C:"LocalAccountTokenFilterPolicy"') do set "FDS_POLICY_BEFORE=%%A"
        if not defined FDS_POLICY_BEFORE (
            echo [FAILED] Could not read the original LocalAccountTokenFilterPolicy value.
            goto :FAIL
        )
    )

    set "FDS_START_MODE="
    for /f "tokens=3" %%A in ('sc.exe qc WinRM ^| findstr /I /C:"START_TYPE"') do set "FDS_START_MODE=%%A"
    if not defined FDS_START_MODE (
        echo [FAILED] Could not read the original WinRM startup mode.
        goto :FAIL
    )
    set "FDS_RUNNING=STOPPED"
    sc.exe query WinRM | findstr /I /C:"RUNNING" >nul 2>&1
    if not errorlevel 1 set "FDS_RUNNING=RUNNING"

    >"%FDS_STATE_FILE%" echo POLICY=!FDS_POLICY_BEFORE!
    >>"%FDS_STATE_FILE%" echo START_MODE=!FDS_START_MODE!
    >>"%FDS_STATE_FILE%" echo RUNNING=!FDS_RUNNING!
    if not exist "%FDS_STATE_FILE%" (
        echo [FAILED] Could not save the original WinRM state.
        goto :FAIL
    )
    echo [OK] Original state saved to: %FDS_STATE_FILE%
) else (
    echo [OK] Existing original state kept: %FDS_STATE_FILE%
)

echo [3/8] Configure WinRM service and listener...
winrm.exe quickconfig -quiet
if errorlevel 1 (
    echo [FAILED] WinRM quickconfig failed.
    set "FDS_FAILED=1"
)
sc.exe config WinRM start= auto >nul
if errorlevel 1 (
    echo [FAILED] Could not set WinRM startup mode to Automatic.
    set "FDS_FAILED=1"
)
sc.exe start WinRM >nul 2>&1
sc.exe query WinRM | findstr /I /C:"RUNNING" >nul 2>&1
if errorlevel 1 (
    echo [FAILED] WinRM service is not running.
    set "FDS_FAILED=1"
) else (
    echo [OK] WinRM service is running.
)

echo [4/8] Verify HTTP 5985 listener...
winrm.exe enumerate winrm/config/listener >"%FDS_LISTENER_FILE%" 2>&1
if errorlevel 1 (
    echo [FAILED] Could not read WinRM listeners.
    set "FDS_FAILED=1"
) else (
    findstr /I /C:"Transport = HTTP" "%FDS_LISTENER_FILE%" >nul
    if errorlevel 1 (
        echo [FAILED] HTTP listener was not found.
        set "FDS_FAILED=1"
    )
    findstr /I /C:"Port = 5985" "%FDS_LISTENER_FILE%" >nul
    if errorlevel 1 (
        echo [FAILED] HTTP listener is not using port 5985.
        set "FDS_FAILED=1"
    )
)
netstat -ano -p tcp | findstr /R /C:":5985 .*LISTENING" >nul
if errorlevel 1 (
    echo [FAILED] TCP 5985 is not in LISTENING state.
    set "FDS_FAILED=1"
) else (
    echo [OK] TCP 5985 is listening.
)

echo [5/8] Verify an enabled firewall rule allows TCP 5985...
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; if(-not (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)){ Write-Error 'NetSecurity firewall cmdlets are unavailable.'; exit 5 }; $ok=$false; foreach($r in @(Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow -ErrorAction Stop)){ foreach($f in @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $r -ErrorAction SilentlyContinue)){ if(([string]$f.Protocol -ieq 'TCP') -and ([string]$f.LocalPort -match '(^|,)\s*5985\s*(,|$)')){ $ok=$true; break } }; if($ok){ break } }; if(-not $ok){ Write-Error 'No enabled inbound firewall rule for TCP 5985 was found.'; exit 6 }; Write-Host '[OK] An enabled inbound firewall rule allows TCP 5985.'; exit 0"
if errorlevel 1 set "FDS_FAILED=1"

echo [6/8] Enable the local-admin remote token policy...
reg.exe add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /t REG_DWORD /d 1 /f >nul
if errorlevel 1 (
    echo [FAILED] Could not set LocalAccountTokenFilterPolicy=1.
    set "FDS_FAILED=1"
)
set "FDS_POLICY_NOW="
for /f "tokens=3" %%A in ('reg.exe query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy ^| findstr /I /C:"LocalAccountTokenFilterPolicy"') do set "FDS_POLICY_NOW=%%A"
if /I not "!FDS_POLICY_NOW!"=="0x1" (
    echo [FAILED] LocalAccountTokenFilterPolicy verification failed. Current value: !FDS_POLICY_NOW!
    set "FDS_FAILED=1"
) else (
    echo [OK] LocalAccountTokenFilterPolicy=1.
)

echo [7/8] Verify WinRM authentication configuration...
winrm.exe get winrm/config/service/auth >"%FDS_AUTH_FILE%" 2>&1
if errorlevel 1 (
    echo [FAILED] Could not read WinRM service authentication settings.
    set "FDS_FAILED=1"
) else (
    findstr /I /C:"Negotiate = true" "%FDS_AUTH_FILE%" >nul
    if errorlevel 1 (
        echo [FAILED] WinRM Negotiate authentication is not enabled.
        set "FDS_FAILED=1"
    ) else (
        echo [OK] WinRM Negotiate authentication is enabled.
    )
)

echo [8/8] Run local WinRM identity check...
winrm.exe id >nul
if errorlevel 1 (
    echo [FAILED] Local WinRM identity check failed.
    set "FDS_FAILED=1"
) else (
    echo [OK] Local WinRM identity check passed.
)

del /q "%FDS_LISTENER_FILE%" "%FDS_AUTH_FILE%" >nul 2>&1
echo.
if "%FDS_FAILED%"=="0" (
    echo SUCCESS: Target host is prepared for ADMS WinRM.
    echo Next: return to File Distribution Studio and click "Test WinRM".
    echo The client/management machine does not need WinRM setup.
    pause
    endlocal
    exit /b 0
)

:FAIL
del /q "%FDS_LISTENER_FILE%" "%FDS_AUTH_FILE%" >nul 2>&1
echo.
echo FAILED: Target preparation did not complete. Do not test distribution yet.
echo Fix the reported item, then run this script again as Administrator.
pause
endlocal
exit /b 1
