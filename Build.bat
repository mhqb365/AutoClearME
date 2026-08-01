@echo off
setlocal
cd /d "%~dp0"

echo Packaging portable app to dist...

if exist "dist" rmdir /s /q "dist"
if exist "dist" (
  echo Failed to remove existing dist folder.
  echo Close any app, terminal, or Explorer window using dist, then run Build.bat again.
  pause
  exit /b 1
)
mkdir "dist"
if errorlevel 1 (
  echo Failed to create dist folder.
  pause
  exit /b 1
)

copy /y "AutoClearME.py" "dist\" >nul
copy /y "AutoClearME_GUI.py" "dist\" >nul
copy /y "AutoClearME_Update.py" "dist\" >nul
copy /y "Run.bat" "dist\" >nul
copy /y "languages.json" "dist\" >nul
copy /y "icon.ico" "dist\" >nul
copy /y "VERSION" "dist\" >nul
copy /y "config.example.json" "dist\" >nul
if exist "README.md" copy /y "README.md" "dist\" >nul
if exist "LICENSE" copy /y "LICENSE" "dist\" >nul

if exist "dist\MEA" rmdir /s /q "dist\MEA"
xcopy "MEA" "dist\MEA\" /e /i /y >nul
if errorlevel 1 (
  echo Failed to copy MEA folder.
  pause
  exit /b 1
)

echo.
echo Done: dist\
set /p APP_VERSION=<"VERSION"
set "RELEASE_ROOT=AutoClearME_%APP_VERSION%"
set "RELEASE_ZIP=AutoClearME_%APP_VERSION%.zip"
if exist "release" rmdir /s /q "release"
mkdir "release\%RELEASE_ROOT%"
if errorlevel 1 (
  echo Failed to create release staging folder.
  pause
  exit /b 1
)
xcopy "dist" "release\%RELEASE_ROOT%\" /e /i /y >nul
if errorlevel 1 (
  echo Failed to copy dist into release staging folder.
  pause
  exit /b 1
)
if exist "%RELEASE_ZIP%" del /q "%RELEASE_ZIP%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'release\%RELEASE_ROOT%' -DestinationPath '%RELEASE_ZIP%' -Force"
if errorlevel 1 (
  echo Failed to create %RELEASE_ZIP%.
  pause
  exit /b 1
)
rmdir /s /q "release"
echo Release ZIP: %RELEASE_ZIP%
echo ZIP extracts to %RELEASE_ROOT%\.
echo Users can run %RELEASE_ROOT%\Run.bat to start Auto Clear ME.
pause
