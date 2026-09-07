@echo off
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

call :banner "File Distribution Studio - Python environment setup"
if not exist "%REQ%" (
  echo [ERROR] requirements.txt was not found: %REQ%
  exit /b 1
)

if /I "%MODE%"=="recreate" (
  if exist "%VENV%" (
    echo [1/5] Removing old .venv ...
    rmdir /s /q "%VENV%"
    if exist "%VENV%" (
      echo [ERROR] .venv is still in use. Close VS Code terminals and Python processes, then retry.
      exit /b 1
    )
  )
)

if /I "%MODE%"=="verify" goto :verify_only

if /I "%MODE%"=="ensure" if exist "%VENV_PY%" (
  call :verify_quick >nul 2>&1
  if not errorlevel 1 (
    echo Python environment is ready.
    exit /b 0
  )
  echo Existing .venv is incomplete. Repairing dependencies ...
)

if exist "%VENV_PY%" goto :install

echo [1/5] Looking for 64-bit Python 3.10-3.14 ...
call :find_python
if errorlevel 1 exit /b 1

echo [2/5] Creating .venv ...
call :run_bootstrap -m venv "%VENV%"
if errorlevel 1 (
  echo [ERROR] Failed to create the Python virtual environment.
  exit /b 1
)

:install
if not exist "%VENV_PY%" (
  echo [ERROR] Virtual-environment Python was not found: %VENV_PY%
  exit /b 1
)

echo [3/5] Checking pip ...
"%VENV_PY%" -m ensurepip --upgrade >nul 2>&1
"%VENV_PY%" -m pip --version
if errorlevel 1 (
  echo [ERROR] pip is not available inside .venv.
  exit /b 1
)

echo [4/5] Installing / repairing application dependencies ...
echo Note: this script does not force a pip self-upgrade.
"%VENV_PY%" -m pip install --disable-pip-version-check --no-input --prefer-binary -r "%REQ%"
if errorlevel 1 (
  echo [ERROR] Dependency installation failed.
  echo Check access to your Python package index / proxy, then run setup.bat again.
  exit /b 1
)

echo [5/5] Verifying the runtime environment ...
call :verify_environment
if errorlevel 1 exit /b 1

echo.
call :banner "Environment setup completed"
echo Start the application with run.bat.
exit /b 0

:verify_only
if not exist "%VENV_PY%" (
  echo [ERROR] .venv does not exist. Run setup.bat first.
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
  echo [ERROR] Missing dependencies or unsupported Python version / architecture.
  exit /b 1
)
"%VENV_PY%" -m pip check
if errorlevel 1 (
  echo [ERROR] pip dependency check failed.
  exit /b 1
)
"%VENV_PY%" main.py --self-test
if errorlevel 1 (
  echo [ERROR] Application self-test failed.
  exit /b 1
)
"%VENV_PY%" -c "import sys,PySide6,paramiko,psutil,winrm,impacket; print('Python:',sys.version.split()[0]); print('PySide6:',PySide6.__version__); print('paramiko:',paramiko.__version__); print('psutil:',psutil.__version__)"
exit /b 0

:find_python
for %%V in (3.14 3.13 3.12 3.11 3.10) do call :try_py_version %%V
if defined BOOTSTRAP_EXE exit /b 0

for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined BOOTSTRAP_EXE set "BOOTSTRAP_EXE=%%P"
if defined BOOTSTRAP_EXE (
  "%BOOTSTRAP_EXE%" -c "import sys,struct; assert struct.calcsize('P')*8==64; assert (3,10) <= sys.version_info[:2] < (3,15)" >nul 2>&1
  if not errorlevel 1 (
    set "BOOTSTRAP_SELECTOR="
    echo Selected: python
    exit /b 0
  )
)
set "BOOTSTRAP_EXE="
echo [ERROR] 64-bit Python 3.10-3.14 was not found.
echo Install Python x64 and make sure py.exe or python.exe is available in PATH.
exit /b 1

:try_py_version
if defined BOOTSTRAP_EXE exit /b 0
where py.exe >nul 2>&1
if errorlevel 1 exit /b 0
py -%~1 -c "import sys,struct; assert struct.calcsize('P')*8==64; assert sys.version_info[:2]==tuple(map(int,'%~1'.split('.')))" >nul 2>&1
if errorlevel 1 exit /b 0
for /f "delims=" %%P in ('where py.exe 2^>nul') do if not defined BOOTSTRAP_EXE set "BOOTSTRAP_EXE=%%P"
set "BOOTSTRAP_SELECTOR=-%~1"
echo Selected: py -%~1
exit /b 0

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
