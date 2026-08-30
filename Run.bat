@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0AutoClearME.exe" (
  start "" "%~dp0AutoClearME.exe"
  exit /b 0
)

call :find_python
if not defined PYTHON_EXE (
  echo Python was not found on this computer.
  echo.
  echo Please install Python 3.10 or newer from:
  echo https://www.python.org/downloads/windows/
  echo.
  echo During setup, enable "Add python.exe to PATH", then run this file again.
  pause
  exit /b 1
)

"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  echo Current Python:
  "%PYTHON_EXE%" --version
  pause
  exit /b 1
)

"%PYTHON_EXE%" -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo The installed Python does not include tkinter.
  echo Please install the official Python release from python.org, then run this file again.
  pause
  exit /b 1
)

set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PYW=%VENV_DIR%\Scripts\pythonw.exe"

if not exist "%VENV_PY%" (
  echo Preparing Auto Clear ME Python environment...
  "%PYTHON_EXE%" -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo Failed to create Python virtual environment.
    pause
    exit /b 1
  )
)

"%VENV_PY%" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo Preparing pip...
  "%VENV_PY%" -m ensurepip --upgrade >nul 2>nul
  if errorlevel 1 (
    "%PYTHON_EXE%" -m ensurepip --upgrade >nul 2>nul
    "%PYTHON_EXE%" -m venv --upgrade-deps "%VENV_DIR%" >nul 2>nul
  )
)

"%VENV_PY%" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo Downloading pip installer...
  set "GET_PIP=%TEMP%\autoclearme_get-pip.py"
  "%PYTHON_EXE%" -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', r'%GET_PIP%')" >nul 2>nul
  if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%GET_PIP%'" >nul 2>nul
  )
  if not exist "%GET_PIP%" (
    echo Failed to download pip installer.
    echo Check your internet connection, then run this file again.
    pause
    exit /b 1
  )
  "%VENV_PY%" "%GET_PIP%"
  if errorlevel 1 (
    echo Failed to install pip automatically.
    echo Check your internet connection, then run this file again.
    pause
    exit /b 1
  )
)

"%VENV_PY%" -c "import colorama, crccheck, pltable, biosutilities, tkinterdnd2" >nul 2>nul
if errorlevel 1 (
  echo Installing Auto Clear ME dependencies...
  "%VENV_PY%" -m pip install --upgrade pip
  if errorlevel 1 (
    echo Failed to update pip.
    pause
    exit /b 1
  )
  "%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo Failed to install dependencies.
    echo Check your internet connection, then run this file again.
    pause
    exit /b 1
  )
)

"%VENV_PY%" -c "import colorama, crccheck, pltable, biosutilities, tkinterdnd2" >nul 2>nul
if errorlevel 1 (
  echo Python dependencies are still missing or broken.
  echo Delete the .venv folder and run this file again.
  pause
  exit /b 1
)

if not exist "%VENV_PYW%" set "VENV_PYW=%VENV_PY%"
start "" "%VENV_PYW%" "%~dp0AutoClearME_GUI.py"
exit /b 0

:find_python
for %%C in ("py -3" "python" "python3") do (
  for /f "usebackq delims=" %%P in (`%%~C -c "import sys; print(sys.executable)" 2^>nul`) do (
    set "PYTHON_EXE=%%P"
    exit /b 0
  )
)
exit /b 0
