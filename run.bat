@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

call setup.bat --ensure
if errorlevel 1 (
  echo.
  echo 文件分发工作台无法自动准备 Python 运行环境。
  echo 请根据上方错误处理后，再重新运行 run.bat。
  pause
  exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" main.py
