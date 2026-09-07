@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

set "ROOT=%CD%"
set "VENV=%ROOT%\.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "REQ=%ROOT%\requirements.txt"
set "MODE=setup"
set "BOOTSTRAP_EXE="
set "BOOTSTRAP_SELECTOR="

if /I "%~1"=="--ensure" set "MODE=ensure"
if /I "%~1"=="--verify" set "MODE=verify"
if /I "%~1"=="--recreate" set "MODE=recreate"

call :banner "文件分发工作台 - Python 环境初始化"
if not exist "%REQ%" (
  echo [错误] 未找到 requirements.txt：%REQ%
  exit /b 1
)

if /I "%MODE%"=="recreate" (
  if exist "%VENV%" (
    echo [1/5] 正在删除旧的 .venv ...
    rmdir /s /q "%VENV%"
    if exist "%VENV%" (
      echo [错误] .venv 仍被占用。请关闭 VS Code 终端或相关 Python 进程后重试。
      exit /b 1
    )
  )
)

if /I "%MODE%"=="verify" goto :verify_only

if /I "%MODE%"=="ensure" if exist "%VENV_PY%" (
  call :verify_quick >nul 2>&1
  if not errorlevel 1 (
    echo Python 虚拟环境已就绪。
    exit /b 0
  )
  echo 检测到 .venv 依赖不完整，正在自动修复...
)

if exist "%VENV_PY%" goto :install

echo [1/5] 正在查找 64 位 Python 3.10-3.14 ...
call :find_python
if errorlevel 1 exit /b 1

echo [2/5] 正在创建 .venv ...
call :run_bootstrap -m venv "%VENV%"
if errorlevel 1 (
  echo [错误] Python 虚拟环境创建失败。
  exit /b 1
)

:install
if not exist "%VENV_PY%" (
  echo [错误] 未找到虚拟环境 Python：%VENV_PY%
  exit /b 1
)

echo [3/5] 正在检查 pip ...
"%VENV_PY%" -m ensurepip --upgrade >nul 2>&1
"%VENV_PY%" -m pip --version
if errorlevel 1 (
  echo [错误] .venv 中的 pip 不可用。
  exit /b 1
)

echo [4/5] 正在安装 / 修复程序依赖 ...
echo 提示：这里不会强制升级 pip，只安装 requirements.txt 中的程序依赖。
"%VENV_PY%" -m pip install --disable-pip-version-check --no-input --prefer-binary -r "%REQ%"
if errorlevel 1 (
  echo [错误] 依赖安装失败。
  echo 请检查当前电脑到 Python 软件源的网络 / 代理配置，然后重新运行 setup.bat。
  exit /b 1
)

echo [5/5] 正在验证运行环境 ...
call :verify_environment
if errorlevel 1 exit /b 1

echo.
call :banner "环境准备完成"
echo 以后直接双击 run.bat 即可启动程序。
exit /b 0

:verify_only
if not exist "%VENV_PY%" (
  echo [错误] .venv 尚未创建，请先运行 setup.bat。
  exit /b 1
)
call :verify_environment
exit /b %ERRORLEVEL%

:verify_quick
"%VENV_PY%" -c "import sys,struct; assert sys.platform=='win32'; assert struct.calcsize('P')*8==64; assert (3,10) <= sys.version_info[:2] < (3,15); import PySide6,paramiko,psutil,winrm,impacket; print('OK')"
exit /b %ERRORLEVEL%

:verify_environment
call :verify_quick
if errorlevel 1 (
  echo [错误] 缺少程序依赖，或者 Python 版本 / 架构不受支持。
  exit /b 1
)
"%VENV_PY%" -m pip check
if errorlevel 1 (
  echo [错误] pip 依赖检查失败。
  exit /b 1
)
"%VENV_PY%" main.py --self-test
if errorlevel 1 (
  echo [错误] 程序自检失败。
  exit /b 1
)
"%VENV_PY%" -c "import sys,PySide6,paramiko,psutil,winrm,impacket; print('Python:',sys.version.split()[0]); print('PySide6:',PySide6.__version__); print('paramiko:',paramiko.__version__); print('psutil:',psutil.__version__)"
exit /b 0

:find_python
for /f "delims=" %%P in ('where py.exe 2^>nul') do if not defined BOOTSTRAP_EXE set "BOOTSTRAP_EXE=%%P"
if defined BOOTSTRAP_EXE (
  "%BOOTSTRAP_EXE%" -3 -c "import sys,struct; assert struct.calcsize('P')*8==64; assert (3,10) <= sys.version_info[:2] < (3,15)" >nul 2>&1
  if not errorlevel 1 (
    set "BOOTSTRAP_SELECTOR=-3"
    echo 已选择：py -3
    exit /b 0
  )
)
set "BOOTSTRAP_EXE="
for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined BOOTSTRAP_EXE set "BOOTSTRAP_EXE=%%P"
if defined BOOTSTRAP_EXE (
  "%BOOTSTRAP_EXE%" -c "import sys,struct; assert struct.calcsize('P')*8==64; assert (3,10) <= sys.version_info[:2] < (3,15)" >nul 2>&1
  if not errorlevel 1 (
    set "BOOTSTRAP_SELECTOR="
    echo 已选择：python
    exit /b 0
  )
)
echo [错误] 未找到 64 位 Python 3.10-3.14。
echo 请安装 Python x64，并确保 py.exe 或 python.exe 已加入 PATH。
exit /b 1

:run_bootstrap
if defined BOOTSTRAP_SELECTOR (
  "%BOOTSTRAP_EXE%" %BOOTSTRAP_SELECTOR% %*
) else (
  "%BOOTSTRAP_EXE%" %*
)
exit /b %ERRORLEVEL%

:banner
echo ========================================================================
echo  %~1
echo ========================================================================
exit /b 0
