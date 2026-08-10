$ErrorActionPreference = "Stop"

$pythonVersion = "3.11.9"
$runtimeRoot = Join-Path $PSScriptRoot "runtime"
$pythonDir = Join-Path $runtimeRoot "Python"
$pythonExe = Join-Path $pythonDir "python.exe"
$pythonInstaller = Join-Path $runtimeRoot "python-$pythonVersion-amd64.exe"
$getPip = Join-Path $runtimeRoot "get-pip.py"

function Copy-SevenZipCandidate {
    param(
        [string]$SourceExe,
        [string]$ExternalDir
    )
    if (-not (Test-Path $SourceExe)) {
        return $false
    }
    New-Item -ItemType Directory -Force $ExternalDir | Out-Null
    $targetExe = Join-Path $ExternalDir "7z.exe"
    Copy-Item -Force $SourceExe $targetExe
    $sourceDir = Split-Path -Parent $SourceExe
    foreach ($name in @("7z.dll", "7zxa.dll")) {
        $dll = Join-Path $sourceDir $name
        if (Test-Path $dll) {
            Copy-Item -Force $dll (Join-Path $ExternalDir $name)
        }
    }
    return $true
}

function Ensure-SevenZipExternal {
    param(
        [string]$PythonExe,
        [string]$PythonDir
    )
    $externalDir = Join-Path $PythonDir "Lib\site-packages\biosutilities\external"
    if (Test-Path (Join-Path $externalDir "7z.exe")) {
        return
    }
    if (Test-Path (Join-Path $externalDir "7zz.exe")) {
        return
    }

    Write-Host "Preparing bundled 7-Zip for BIOSUtilities..."
    $candidatePaths = @(
        "$env:ProgramFiles\7-Zip\7z.exe",
        "${env:ProgramFiles(x86)}\7-Zip\7z.exe"
    )
    foreach ($candidate in $candidatePaths) {
        if (Copy-SevenZipCandidate -SourceExe $candidate -ExternalDir $externalDir) {
            return
        }
    }

    $toolCache = Join-Path $runtimeRoot "tools"
    $extractDir = Join-Path $toolCache "7zip-extra"
    New-Item -ItemType Directory -Force $toolCache | Out-Null
    if (Test-Path $extractDir) {
        Remove-Item -Recurse -Force $extractDir
    }
    New-Item -ItemType Directory -Force $extractDir | Out-Null

    $sevenZipVersion = "26.02"
    $sevenZipFileVersion = "2602"
    $sevenZr = Join-Path $toolCache "7zr.exe"
    $sevenExtra = Join-Path $toolCache "7z-extra.7z"
    $releaseRoot = "https://github.com/ip7z/7zip/releases/download/$sevenZipVersion"
    curl.exe -L -o $sevenZr "$releaseRoot/7zr.exe"
    curl.exe -L -o $sevenExtra "$releaseRoot/7z$sevenZipFileVersion-extra.7z"
    if (-not (Test-Path $sevenZr) -or -not (Test-Path $sevenExtra)) {
        throw "Failed to download 7-Zip external tools."
    }
    $extract = Start-Process -FilePath $sevenZr -ArgumentList @("x", $sevenExtra, "-o$extractDir", "-y") -Wait -PassThru -WindowStyle Hidden
    if ($extract.ExitCode -ne 0) {
        throw "Failed to extract 7-Zip external tools."
    }

    $sevenExe = Get-ChildItem -Path $extractDir -Recurse -File -Include "7zz.exe", "7za.exe", "7z.exe" |
        Where-Object { $_.FullName -match "\\x64\\" -or $_.Name -eq "7zz.exe" } |
        Select-Object -First 1
    if (-not $sevenExe) {
        throw "7-Zip external executable was not found after extraction."
    }
    Copy-SevenZipCandidate -SourceExe $sevenExe.FullName -ExternalDir $externalDir | Out-Null
}

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
$requirements = Join-Path $PSScriptRoot "requirements.txt"
if (Test-Path $requirements) {
    & $pythonExe -m pip install --upgrade --no-warn-script-location -r $requirements
} else {
    & $pythonExe -m pip install --upgrade --no-warn-script-location colorama crccheck pltable biosutilities
}
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}
Ensure-SevenZipExternal -PythonExe $pythonExe -PythonDir $pythonDir

Write-Host "Verifying bundled dependencies..."
& $pythonExe -c "import tkinter, colorama, crccheck, pltable, biosutilities; from biosutilities.common.externals import szip_path; print('7-Zip:', szip_path()); print('runtime ok')"

Write-Host "Python runtime ready: $pythonDir"
