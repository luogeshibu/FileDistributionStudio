@echo off
chcp 65001 >nul
setlocal EnableExtensions

echo ============================================================
echo File Distribution Studio - WinRM 本地管理员模式
echo ============================================================
echo 用途：目标机使用 ADMS 等“本地 Administrators 成员账号”进行 WinRM 远程管理。
echo.
echo [重要]
echo 本脚本除了初始化/启动 WinRM，还会设置：
echo   LocalAccountTokenFilterPolicy = 1
echo 这会允许本地管理员通过网络获得完整管理员令牌。
echo 仅应在受控内网、明确授权的运维环境中使用。
echo.
echo 执行前会记录原始状态，可用配套的 TARGET_RESTORE_WINRM_LOCAL_ADMIN.cmd 恢复。
echo 请在目标 Windows 上“以管理员身份运行”。
echo.

net session >nul 2>&1
if errorlevel 1 (
  echo [错误] 当前窗口不是管理员权限。
  echo 请右键此脚本，选择“以管理员身份运行”。
  pause
  exit /b 1
)

choice /C YN /N /M "确认启用 WinRM 本地管理员远程模式？[Y/N]: "
if errorlevel 2 (
  echo 已取消。
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
  echo [备份] 已记录原始 LocalAccountTokenFilterPolicy 状态：%FDS_STATE_FILE%
) else (
  echo [备份] 已存在原始状态文件，不覆盖：%FDS_STATE_FILE%
)

echo.
echo [1/8] 初始化 WinRM
winrm quickconfig -quiet

echo.
echo [2/8] 服务状态
sc query WinRM

echo.
echo [3/8] 设置自动启动并启动
sc config WinRM start= auto
sc start WinRM

echo.
echo [4/8] 允许本地管理员远程获得完整管理员令牌
reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /t REG_DWORD /d 1 /f
if errorlevel 1 (
  echo [错误] 注册表设置失败，脚本停止。
  pause
  exit /b 1
)

echo.
echo [5/8] 重启 WinRM 服务
sc stop WinRM
sc start WinRM

echo.
echo [6/8] Listener
winrm enumerate winrm/config/listener

echo.
echo [7/8] 5985 监听
netstat -ano | findstr :5985

echo.
echo [8/8] 本机 WinRM 与策略确认
winrm id
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy

echo.
echo 完成。
echo 请回到管理机，在 File Distribution Studio 中使用本地账号（例如 ADMS）点击“测试 WinRM”。
echo 若后续需要恢复原策略，请运行 TARGET_RESTORE_WINRM_LOCAL_ADMIN.cmd。
pause
