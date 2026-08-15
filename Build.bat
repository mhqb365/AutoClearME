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
copy /y "CreateShortcut.bat" "dist\" >nul
copy /y "languages.json" "dist\" >nul
copy /y "icon.ico" "dist\" >nul
copy /y "VERSION" "dist\" >nul
copy /y "requirements.txt" "dist\" >nul
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
if exist "dist\MEA\__CHECK__" rmdir /s /q "dist\MEA\__CHECK__"

echo.
echo Done: dist\
echo Users can run dist\Run.bat to start Auto Clear ME.
echo Python 3.10+ must be installed on the computer; Run.bat will prepare dependencies automatically.
pause
