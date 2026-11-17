@echo off
setlocal EnableExtensions

rem Build an unsigned Windows application from a Windows machine. Instagram
rem exports and local caches are never bundled into the executable.
set "PROJECT_DIR=%~dp0.."
for %%I in ("%PROJECT_DIR%") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%"

set "IG_VIEWER_PYTHON="
if defined VIRTUAL_ENV (
    set "IG_VIEWER_PYTHON=%VIRTUAL_ENV%\Scripts\python.exe"
) else (
    for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)"') do set "IG_VIEWER_PYTHON=%%I"
)
if not exist "%IG_VIEWER_PYTHON%" (
    echo Install Python 3.10 or newer from python.org, then run this script again.
    exit /b 1
)

set "BUILD_DIR=%TEMP%\ig-viewer-build-%RANDOM%%RANDOM%"
set "APP_DIR=%PROJECT_DIR%\dist\IG Viewer"
set "ZIP_PATH=%PROJECT_DIR%\dist\IG Viewer Windows.zip"

echo Installing build requirements...
"%IG_VIEWER_PYTHON%" -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1

echo Building IG Viewer.exe...
rem Avoid bundling DLLs supplied by unrelated tools on PATH.
set "PATH=%SystemRoot%\System32;%SystemRoot%"
"%IG_VIEWER_PYTHON%" -m PyInstaller --noconfirm ^
    --workpath "%BUILD_DIR%\work" --distpath "%PROJECT_DIR%\dist" scripts\ig_viewer.spec
if errorlevel 1 exit /b 1

"%IG_VIEWER_PYTHON%" -m zipfile -c "%ZIP_PATH%" "%APP_DIR%"
if errorlevel 1 exit /b 1

echo.
echo Built executable: "%APP_DIR%\IG Viewer.exe"
echo Built archive:    "%ZIP_PATH%"
echo This build is unsigned. Windows may show a SmartScreen warning when it is
echo opened on another computer; inspect the publisher/source before choosing
echo "More info" then "Run anyway".

endlocal
