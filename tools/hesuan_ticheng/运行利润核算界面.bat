@echo off
setlocal
cd /d "%~dp0"
python hesuan_gui.py
if errorlevel 1 (
  echo.
  echo 界面启动失败，请检查 Python 环境或错误信息。
  pause
)
