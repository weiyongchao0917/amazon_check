@echo off
setlocal
cd /d "%~dp0"
python hesuan_ticheng.py %*
if errorlevel 1 (
  echo.
  echo 核算失败，请检查上面的错误信息。
  pause
)
