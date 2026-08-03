$ErrorActionPreference = "Stop"

$pythonVersion = "3.11.9"
$runtimeRoot = Join-Path $PSScriptRoot "runtime"
$pythonDir = Join-Path $runtimeRoot "Python"
$pythonExe = Join-Path $pythonDir "python.exe"
$pythonInstaller = Join-Path $runtimeRoot "python-$pythonVersion-amd64.exe"
$getPip = Join-Path $runtimeRoot "get-pip.py"

New-Item -ItemType Directory -Force $runtimeRoot | Out-Null

$needsInstall = -not (Test-Path $pythonExe)
if (-not $needsInstall) {
    $check = Start-Process -FilePath $pythonExe -ArgumentList @("-c", "import tkinter") -Wait -PassThru -WindowStyle Hidden
    if ($check.ExitCode -ne 0) {
        Write-Host "Existing Python runtime does not include Tcl/Tk. Replacing it..."
        $needsInstall = $true
    }
}

if ($needsInstall) {
    Write-Host "Downloading Python installer $pythonVersion..."
    $url = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe"
    curl.exe -L -o $pythonInstaller $url

    if (Test-Path $pythonDir) {
        Remove-Item -Recurse -Force $pythonDir
    }

    Write-Host "Installing Python runtime with Tcl/Tk..."
    New-Item -ItemType Directory -Force $pythonDir | Out-Null
    $args = @(
        "/quiet",
        "InstallAllUsers=0",
        "TargetDir=$pythonDir",
        "Include_launcher=0",
        "InstallLauncherAllUsers=0",
        "Include_tcltk=1",
        "Include_pip=1",
        "Include_test=0",
        "Include_doc=0",
        "Shortcuts=0",
        "AssociateFiles=0",
        "PrependPath=0"
    )
    $process = Start-Process -FilePath $pythonInstaller -ArgumentList $args -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Python installer failed with exit code $($process.ExitCode)"
    }
}

if (-not (Test-Path $pythonExe)) {
    Write-Host "Installer did not create TargetDir. Copying an existing Python 3.11 installation..."
    $sourcePrefix = $null
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $sourcePrefix = (& py -3.11 -c "import sys; print(sys.prefix)" 2>$null)
    }
    if (-not $sourcePrefix) {
        $pythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonCmd) {
            $sourcePrefix = (& python -c "import sys, tkinter; print(sys.prefix)" 2>$null)
        }
    }
    if (-not $sourcePrefix -or -not (Test-Path (Join-Path $sourcePrefix "python.exe"))) {
        throw "Python runtime was not created and no existing Python with tkinter was found."
    }
    if (Test-Path $pythonDir) {
        Remove-Item -Recurse -Force $pythonDir
    }
    New-Item -ItemType Directory -Force $pythonDir | Out-Null
    Copy-Item -Recurse -Force (Join-Path $sourcePrefix "*") $pythonDir
}

if (-not (Test-Path $pythonExe)) {
    throw "Python runtime was not created: $pythonExe"
}

Write-Host "Installing bundled dependencies..."
$requirements = Join-Path $root "requirements.txt"
if (Test-Path $requirements) {
    & $pythonExe -m pip install --upgrade --no-warn-script-location -r $requirements
} else {
    & $pythonExe -m pip install --upgrade --no-warn-script-location colorama crccheck pltable
}
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

Write-Host "Verifying bundled dependencies..."
& $pythonExe -c "import tkinter, colorama, crccheck, pltable; print('runtime ok')"

Write-Host "Python runtime ready: $pythonDir"
