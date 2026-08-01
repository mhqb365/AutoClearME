#!/usr/bin/env python3
"""Download and apply a portable Auto Clear ME release update."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


KEEP_FILES = {"config.json"}
REQUIRED_FILES = [
    "Run.bat",
    "AutoClearME.py",
    "AutoClearME_GUI.py",
    "VERSION",
    str(Path("MEA") / "MEA.py"),
]


def wait_for_parent(pid: int) -> None:
    if pid <= 0 or os.name != "nt":
        return
    for _ in range(120):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.5)


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "AutoClearME-Updater"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as output:
        shutil.copyfileobj(response, output)


def find_payload_root(extract_dir: Path) -> Path:
    candidates = [p for p in extract_dir.rglob("Run.bat") if p.is_file()]
    if not candidates:
        raise RuntimeError("Run.bat was not found in the release ZIP.")
    candidates.sort(key=lambda p: len(p.parts))
    return candidates[0].parent


def validate_payload(payload: Path) -> None:
    missing = [name for name in REQUIRED_FILES if not (payload / name).exists()]
    if missing:
        raise RuntimeError("Release ZIP is missing required files: " + ", ".join(missing))


def replace_app(payload: Path, app_dir: Path) -> None:
    for item in app_dir.iterdir():
        if item.name in KEEP_FILES:
            continue
        if item.name.lower() == "autoclearme_update.py":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    for item in payload.iterdir():
        target = app_dir / item.name
        if item.name in KEEP_FILES and target.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def relaunch(app_dir: Path) -> None:
    launcher = app_dir / "Run.bat"
    if launcher.exists():
        subprocess.Popen(["cmd", "/c", "start", "", str(launcher)], cwd=str(app_dir), shell=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply an Auto Clear ME portable update.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    if (app_dir / ".git").exists():
        raise RuntimeError("Refusing to update a git working tree. Use Build.bat and test updates from dist.")
    wait_for_parent(args.parent_pid)

    with tempfile.TemporaryDirectory(prefix="AutoClearME_Update_") as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / "release.zip"
        extract_dir = tmp_dir / "extract"
        download(args.url, archive)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_dir)
        payload = find_payload_root(extract_dir)
        validate_payload(payload)
        replace_app(payload, app_dir)

    relaunch(app_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
