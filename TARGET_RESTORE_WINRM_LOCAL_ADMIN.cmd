@echo off
chcp 65001 >nul
setlocal EnableExtensions

echo ============================================================
echo File Distribution Studio - 恢复本地管理员远程令牌策略
echo ============================================================
echo 该脚本只恢复 LocalAccountTokenFilterPolicy 到配置脚本执行前记录的状态。
echo 不会停止 WinRM 服务，也不会修改其他 WinRM 配置。
echo.

net session >nul 2>&1
if errorlevel 1 (
  echo [错误] 当前窗口不是管理员权限。
  echo 请右键此脚本，选择“以管理员身份运行”。
  pause
  exit /b 1
)

set "FDS_STATE_FILE=%ProgramData%\FileDistributionStudio\LocalAccountTokenFilterPolicy.before.txt"
if not exist "%FDS_STATE_FILE%" (
  echo [错误] 找不到原始状态文件：
  echo %FDS_STATE_FILE%
  echo 为避免误改注册表，本脚本不会猜测原值。
  pause
  exit /b 1
)

set /p FDS_ORIGINAL=<"%FDS_STATE_FILE%"
echo 检测到原始状态：%FDS_ORIGINAL%
choice /C YN /N /M "确认恢复？[Y/N]: "
if errorlevel 2 exit /b 2

if /I "%FDS_ORIGINAL%"=="MISSING" (
  reg delete HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /f
) else (
  reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /t REG_DWORD /d %FDS_ORIGINAL% /f
)

if errorlevel 1 (
  echo [错误] 恢复失败。
  pause
  exit /b 1
)

echo 恢复完成。
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy
pause
