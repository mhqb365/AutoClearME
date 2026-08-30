@echo off
setlocal
cd /d "%~dp0"

call :find_python
if not defined PYTHON_EXE (
  echo Python was not found on this computer.
  echo Install Python 3.10 or newer, then run BuildExe.bat again.
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

if exist "build" rmdir /s /q "build"
if exist "dist\AutoClearME" rmdir /s /q "dist\AutoClearME"

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
  --add-data "MEA;MEA" ^
  --hidden-import AutoClearME ^
  --hidden-import tkinterdnd2 ^
  --collect-all biosutilities ^
  AutoClearME_GUI.py

if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

if exist "config.example.json" copy /y "config.example.json" "dist\AutoClearME\" >nul
if exist "VERSION" copy /y "VERSION" "dist\AutoClearME\" >nul
if exist "Run.bat" copy /y "Run.bat" "dist\AutoClearME\" >nul
if exist "AutoClearME_Update.py" copy /y "AutoClearME_Update.py" "dist\AutoClearME\" >nul
if exist "README.md" copy /y "README.md" "dist\AutoClearME\" >nul
if exist "LICENSE" copy /y "LICENSE" "dist\AutoClearME\" >nul

if exist "build" rmdir /s /q "build"
if exist "AutoClearME.spec" del /q "AutoClearME.spec"

echo.
echo Done: dist\AutoClearME\AutoClearME.exe
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
