@echo off
setlocal
set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

cd /d "%~dp0"

echo Installing PyInstaller if needed...
"%PY%" -m pip install -q "pyinstaller>=6.0"

echo Generating app icon if possible...
"%PY%" -c "import sys; from PyQt5.QtWidgets import QApplication; from whiteboard_tool import _ensure_app_icon; app = QApplication(sys.argv); _ensure_app_icon()" 2>nul

echo Building dist\EkranoRasiklis.exe ...
"%PY%" -m PyInstaller --noconfirm --clean EkranoRasiklis.spec
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Built: "%~dp0dist\EkranoRasiklis.exe"
echo Your Python copy is unchanged. Use run_whiteboard.bat as before.
exit /b 0
