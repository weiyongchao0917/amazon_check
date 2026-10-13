@echo off
setlocal
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name 订单利润核算 hesuan_gui.py
if errorlevel 1 (
  echo.
  echo exe 打包失败。
  pause
  exit /b 1
)
echo.
echo 打包完成：dist\订单利润核算.exe
pause
