@echo off
chcp 65001 >nul
setlocal EnableExtensions

echo ============================================================
echo File Distribution Studio - WinRM 标准准备
echo ============================================================
echo 此脚本只初始化/启动 WinRM，不修改 UAC、注册表、TrustedHosts 或用户权限。
echo 请在目标 Windows 上“以管理员身份运行”。
echo.

net session >nul 2>&1
if errorlevel 1 (
  echo [错误] 当前窗口不是管理员权限。
  echo 请右键此脚本，选择“以管理员身份运行”。
  pause
  exit /b 1
)

echo [1/6] 初始化 WinRM
winrm quickconfig -quiet

echo.
echo [2/6] 服务状态
sc query WinRM

echo.
echo [3/6] 设置自动启动并启动
sc config WinRM start= auto
sc start WinRM

echo.
echo [4/6] Listener
winrm enumerate winrm/config/listener

echo.
echo [5/6] 5985 监听
netstat -ano | findstr :5985

echo.
echo [6/6] 本机 WinRM
winrm id

echo.
echo 完成。请回到管理机，在 File Distribution Studio 中点击“测试 WinRM”。
pause
