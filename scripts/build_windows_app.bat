@echo off
setlocal EnableExtensions

rem Build an unsigned Windows application from a Windows machine. Instagram
rem exports and local caches are never bundled into the executable.
set "PROJECT_DIR=%~dp0.."
for %%I in ("%PROJECT_DIR%") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%"

where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher ^(py^) was not found. Install Python 3.10 or newer from python.org,
    echo select "Add python.exe to PATH", then run this script again.
    exit /b 1
)

set "BUILD_DIR=%TEMP%\ig-viewer-build-%RANDOM%%RANDOM%"
set "APP_DIR=%PROJECT_DIR%\dist\IG Viewer"
set "ZIP_PATH=%PROJECT_DIR%\dist\IG Viewer Windows.zip"

echo Installing build requirements...
py -3 -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1

echo Building IG Viewer.exe...
py -3 -m PyInstaller --noconfirm --windowed --name "IG Viewer" --workpath "%BUILD_DIR%\work" --distpath "%PROJECT_DIR%\dist" --specpath "%BUILD_DIR%\spec" main.py
if errorlevel 1 exit /b 1

if exist "%ZIP_PATH%" del /q "%ZIP_PATH%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%APP_DIR%' -DestinationPath '%ZIP_PATH%' -Force"
if errorlevel 1 exit /b 1

echo.
echo Built executable: "%APP_DIR%\IG Viewer.exe"
echo Built archive:    "%ZIP_PATH%"
echo This build is unsigned. Windows may show a SmartScreen warning when it is
echo opened on another computer; inspect the publisher/source before choosing
echo "More info" then "Run anyway".

endlocal
