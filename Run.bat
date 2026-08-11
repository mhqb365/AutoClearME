@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0Runtime\Python\python.exe"
set "PYTHONW_EXE=%~dp0Runtime\Python\pythonw.exe"

if not exist "%PYTHON_EXE%" (
  echo Bundled Python runtime was not found.
  echo Please download the full portable release ZIP, extract it, then run this file again.
  pause
  exit /b 1
)

"%PYTHON_EXE%" -c "import tkinter, colorama, crccheck, pltable, biosutilities; from biosutilities.common.externals import szip_path; szip_path()" >nul 2>nul
if errorlevel 1 (
  echo Bundled Python GUI runtime, dependencies, or 7-Zip tools are missing.
  echo Rebuild the release with Build.bat or download the full portable release ZIP again.
  pause
  exit /b 1
)

if not exist "%PYTHONW_EXE%" set "PYTHONW_EXE=%PYTHON_EXE%"

start "" "%PYTHONW_EXE%" "%~dp0AutoClearME_GUI.py"
exit /b 0
