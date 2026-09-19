@echo off
setlocal
set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

cd /d "%~dp0"
echo Manual test mode. To run in background, use setup_autostart.bat instead.
echo.
"%PY%" "%~dp0whiteboard_tool.py"
pause
