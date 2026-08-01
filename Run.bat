@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
set "PYTHONW_CMD="

py -3 --version >nul 2>nul
if errorlevel 1 (
  python --version >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
) else (
  set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
  echo Python was not found. Installing Python with winget...
  winget --version >nul 2>nul
  if errorlevel 1 (
    echo winget was not found. Please install Python manually from https://www.python.org/
    pause
    exit /b 1
  )
  winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
  py -3 --version >nul 2>nul
  if errorlevel 1 (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
  ) else (
    set "PYTHON_CMD=py -3"
  )
)

if not defined PYTHON_CMD (
  echo Python was installed, but it is not available in this terminal yet.
  echo Close this window and run this launcher again.
  pause
  exit /b 1
)

py -3w --version >nul 2>nul
if errorlevel 1 (
  pythonw --version >nul 2>nul
  if not errorlevel 1 set "PYTHONW_CMD=pythonw"
) else (
  set "PYTHONW_CMD=py -3w"
)
if not defined PYTHONW_CMD set "PYTHONW_CMD=%PYTHON_CMD%"

echo Installing required Python packages...
%PYTHON_CMD% -m pip install --upgrade pip
%PYTHON_CMD% -m pip install --upgrade colorama crccheck pltable
if errorlevel 1 (
  echo Failed to install required Python packages.
  pause
  exit /b 1
)

start "" %PYTHONW_CMD% "%~dp0AutoClearME_GUI.py"
exit /b 0
