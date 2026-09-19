@echo off
setlocal
set "PY=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
if not exist "%PY%" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=pythonw.exe"
set "SCRIPT=%~dp0whiteboard_tool.py"
set "TASK=WhiteboardHotkeyTool"

schtasks /Delete /TN "%TASK%" /F >nul 2>&1

powershell -NoProfile -Command "$running = Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*whiteboard_tool.py*' }; if ($running) { exit 1 } else { exit 0 }"
if %errorlevel%==1 (
    echo.
    echo Whiteboard is already running in the background.
    echo It will stay active until you log out.
    echo.
    pause
    exit /b 0
)

start "" "%PY%" "%SCRIPT%"

echo.
echo Whiteboard is running in the background.
echo Hold * and - together to open it.
echo.
echo It will keep running until you log out.
echo ^(Optional: run remove_autostart.bat to stop it early.^)
echo.
pause
