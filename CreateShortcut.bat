@echo off
setlocal
cd /d "%~dp0"

set "SHORTCUT_NAME=Auto Clear ME.lnk"
set "TARGET=%~dp0Run.bat"
set "ICON=%~dp0icon.ico"
set "DESKTOP=%USERPROFILE%\Desktop"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$shell = New-Object -ComObject WScript.Shell; " ^
  "$link = $shell.CreateShortcut((Join-Path $env:USERPROFILE 'Desktop\Auto Clear ME.lnk')); " ^
  "$link.TargetPath = '%TARGET%'; " ^
  "$link.WorkingDirectory = '%~dp0'; " ^
  "$link.IconLocation = '%ICON%'; " ^
  "$link.Save()"

if errorlevel 1 (
  echo Failed to create desktop shortcut.
  pause
  exit /b 1
)

echo Desktop shortcut created: %DESKTOP%\%SHORTCUT_NAME%
pause
