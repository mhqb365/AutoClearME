@echo off
setlocal
cd /d "%~dp0"

call :find_python
if not defined PYTHON_EXE (
  echo Python was not found on this computer.
  echo Install Python 3.10 or newer, then run Build.bat again.
  pause
  exit /b 1
)

"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  "%PYTHON_EXE%" --version
  pause
  exit /b 1
)

set "BUILD_VENV=%~dp0.build-venv"
set "BUILD_PY=%BUILD_VENV%\Scripts\python.exe"
set "APP_VERSION="
if exist "VERSION" (
  for /f "usebackq tokens=* delims=" %%V in ("VERSION") do (
    if not defined APP_VERSION set "APP_VERSION=%%V"
  )
)
if not defined APP_VERSION set "APP_VERSION=dev"
set "APP_DIR=AutoClearME_v%APP_VERSION%"
set "BUILD_OUT=dist\AutoClearME"
set "FINAL_OUT=dist\%APP_DIR%"

if not exist "%BUILD_PY%" (
  echo Preparing build environment...
  "%PYTHON_EXE%" -m venv "%BUILD_VENV%"
  if errorlevel 1 (
    echo Failed to create build virtual environment.
    pause
    exit /b 1
  )
)

echo Installing build dependencies...
"%BUILD_PY%" -m pip install --upgrade pip
if errorlevel 1 (
  echo Failed to update pip.
  pause
  exit /b 1
)

"%BUILD_PY%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo Failed to install build dependencies.
  pause
  exit /b 1
)

if exist "build" rmdir /s /q "build" 2>nul
if exist "%BUILD_OUT%" rmdir /s /q "%BUILD_OUT%"
if exist "%FINAL_OUT%" rmdir /s /q "%FINAL_OUT%"

echo Building AutoClearME.exe...
"%BUILD_PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --windowed ^
  --name AutoClearME ^
  --icon icon.ico ^
  --add-data "languages.json;." ^
  --add-data "VERSION;." ^
  --add-data "icon.ico;." ^
  --add-data "icon.png;." ^
  --add-data "MEAnalyzer;MEAnalyzer" ^
  --hidden-import AutoClearME ^
  --hidden-import tkinterdnd2 ^
  --collect-all biosutilities ^
  AutoClearME_GUI.py

if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

echo Building AutoClearME_Update.exe...
"%BUILD_PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name AutoClearME_Update ^
  --icon icon.ico ^
  --add-data "icon.ico;." ^
  AutoClearME_Update.py

if errorlevel 1 (
  echo Updater build failed.
  pause
  exit /b 1
)

if exist "dist\AutoClearME_Update.exe" (
  copy /y "dist\AutoClearME_Update.exe" "%BUILD_OUT%\_internal\" >nul
)
if exist "config.example.json" copy /y "config.example.json" "%BUILD_OUT%\" >nul
if exist "VERSION" copy /y "VERSION" "%BUILD_OUT%\" >nul
if exist "README.md" copy /y "README.md" "%BUILD_OUT%\" >nul
if exist "LICENSE" copy /y "LICENSE" "%BUILD_OUT%\" >nul

move "%BUILD_OUT%" "%FINAL_OUT%" >nul
if errorlevel 1 (
  echo Failed to rename output folder to %APP_DIR%.
  pause
  exit /b 1
)

if exist "build" (
  ping 127.0.0.1 -n 2 >nul
  rmdir /s /q "build" 2>nul
)
if exist "AutoClearME.spec" del /q "AutoClearME.spec"
if exist "AutoClearME_Update.spec" del /q "AutoClearME_Update.spec"
if exist "dist\AutoClearME_Update.exe" del /q "dist\AutoClearME_Update.exe"

echo.
echo Done: %FINAL_OUT%\AutoClearME.exe
echo Double click AutoClearME.exe to open the app.
pause
exit /b 0

:find_python
for %%C in ("py -3" "python" "python3") do (
  for /f "usebackq delims=" %%P in (`%%~C -c "import sys; print(sys.executable)" 2^>nul`) do (
    set "PYTHON_EXE=%%P"
    exit /b 0
  )
)
exit /b 0
