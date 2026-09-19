@echo off
setlocal
cd /d "%~dp0"

set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo Inno Setup 6 not found. Install it and run this again.
    exit /b 1
)

if not exist "%~dp0dist\EkranoRasiklis.exe" (
    echo dist\EkranoRasiklis.exe is missing. Building it first...
    call "%~dp0build_exe.bat"
    if errorlevel 1 exit /b 1
)

echo Building dist\EkranoRasiklis-Setup.exe ...
"%ISCC%" "%~dp0installer\EkranoRasiklis.iss"
if errorlevel 1 (
    echo Setup build failed.
    exit /b 1
)

echo.
echo Built: "%~dp0dist\EkranoRasiklis-Setup.exe"
exit /b 0
