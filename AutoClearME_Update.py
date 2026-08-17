#!/usr/bin/env python3
"""Download and apply a portable Auto Clear ME release update."""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk, messagebox


KEEP_FILES = {"config.json", ".venv"}
REQUIRED_FILES = [
    "Run.bat",
    "AutoClearME.py",
    "AutoClearME_GUI.py",
    "VERSION",
    str(Path("MEA") / "MEA.py"),
]
PROGRESS_QUEUE: queue.Queue[tuple[str, object]] | None = None


def progress(message: str) -> None:
    print(message, flush=True)
    if PROGRESS_QUEUE is not None:
        PROGRESS_QUEUE.put(("message", message))


def progress_done(code: int) -> None:
    if PROGRESS_QUEUE is not None:
        PROGRESS_QUEUE.put(("done", code))


def wait_for_parent(pid: int) -> None:
    if pid <= 0 or os.name != "nt":
        return
    progress("Waiting for Auto Clear ME to close...")
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return
    try:
        ctypes.windll.kernel32.WaitForSingleObject(handle, 60_000)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def download(url: str, target: Path) -> None:
    progress("Downloading update package...")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "AutoClearME-Updater/1.0",
                    "Accept": "application/octet-stream, application/zip, */*",
                },
            )
            with urllib.request.urlopen(request, timeout=90) as response, target.open("wb") as output:
                shutil.copyfileobj(response, output)
            progress("Download complete.")
            return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            progress(f"Download attempt {attempt}/3 failed. Retrying...")
            if attempt < 3:
                time.sleep(2 * attempt)
    raise RuntimeError(
        "Could not download the release ZIP. Check your internet connection, "
        "or download the ZIP manually from GitHub Releases."
    ) from last_error


def normalized_version(value: str) -> str:
    return value.strip().lstrip("vV")


def payload_version(payload: Path) -> str:
    version_file = payload / "VERSION"
    return normalized_version(version_file.read_text(encoding="utf-8")) if version_file.exists() else ""


def find_payload_root(extract_dir: Path, expected_version: str = "") -> Path:
    candidates = [p for p in extract_dir.rglob("Run.bat") if p.is_file()]
    if not candidates:
        raise RuntimeError("Run.bat was not found in the release ZIP.")
    expected = normalized_version(expected_version)
    if expected:
        matching = [candidate for candidate in candidates if payload_version(candidate.parent) == expected]
        if not matching:
            raise RuntimeError(f"Release ZIP does not contain the expected version {expected}.")
        candidates = matching
    candidates.sort(key=lambda p: len(p.parts))
    return candidates[0].parent


def validate_payload(payload: Path, expected_version: str = "") -> None:
    progress("Validating package...")
    missing = [name for name in REQUIRED_FILES if not (payload / name).exists()]
    if missing:
        raise RuntimeError("Release ZIP is missing required files: " + ", ".join(missing))
    expected = normalized_version(expected_version)
    actual = payload_version(payload)
    if expected and actual != expected:
        raise RuntimeError(f"Release version mismatch: expected {expected}, package contains {actual or 'unknown'}.")


def replace_app(payload: Path, app_dir: Path) -> None:
    progress("Replacing app files...")
    for item in app_dir.iterdir():
        if item.name in KEEP_FILES:
            progress(f"Keeping existing: {item.name}")
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
            progress(f"Keeping existing: {item.name}")
            continue
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    progress("App files replaced.")


def relaunch(app_dir: Path) -> None:
    runner = app_dir / "Run.bat"
    if not runner.exists():
        return
    progress("Restarting Auto Clear ME...")
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["cmd", "/c", str(runner)],
        cwd=str(app_dir),
        startupinfo=startupinfo,
        creationflags=creationflags,
    )


def run_update(args: argparse.Namespace) -> int:
    app_dir = Path(args.app_dir).resolve()
    if (app_dir / ".git").exists():
        raise RuntimeError("Refusing to update a git working tree. Use Build.bat and test updates from dist.")
    wait_for_parent(args.parent_pid)

    with tempfile.TemporaryDirectory(prefix="AutoClearME_Update_") as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / "release.zip"
        extract_dir = tmp_dir / "extract"
        download(args.url, archive)
        progress("Extracting package...")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_dir)
        payload = find_payload_root(extract_dir, args.expected_version)
        validate_payload(payload, args.expected_version)
        progress(f"Installing Auto Clear ME {payload_version(payload)}...")
        replace_app(payload, app_dir)

    installed_version = payload_version(app_dir)
    expected_version = normalized_version(args.expected_version)
    if expected_version and installed_version != expected_version:
        raise RuntimeError(
            f"Update verification failed: expected {expected_version}, installed {installed_version or 'unknown'}."
        )
    progress(f"Update verified: Auto Clear ME {installed_version} in {app_dir}")

    try:
        relaunch(app_dir)
    except Exception as exc:
        progress(f"Update was applied, but restart failed: {exc}")
    return 0


class UpdateWindow(tk.Tk):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args
        self.title("Auto Clear ME Update")
        self.geometry("420x140")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.after(1000, lambda: self.attributes("-topmost", False))
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.status_var = tk.StringVar(value="Preparing update...")
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Updating Auto Clear ME", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.status_var, wraplength=380).pack(anchor="w", pady=(10, 8))
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x")
        self.progress.start(12)
        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_width()) // 2
        y = (self.winfo_screenheight() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
        self.after(100, self.poll_queue)

    def poll_queue(self) -> None:
        assert PROGRESS_QUEUE is not None
        try:
            while True:
                kind, value = PROGRESS_QUEUE.get_nowait()
                if kind == "message":
                    self.status_var.set(str(value))
                elif kind == "done":
                    code = int(value)
                    self.progress.stop()
                    if code == 0:
                        self.status_var.set("Update complete. Restarting...")
                        self.after(600, self.destroy)
                    else:
                        self.status_var.set("Update failed.")
                elif kind == "error":
                    messagebox.showerror("Auto Clear ME Update", str(value), parent=self)
        except queue.Empty:
            pass
        self.after(150, self.poll_queue)


def run_update_with_window(args: argparse.Namespace) -> int:
    global PROGRESS_QUEUE
    PROGRESS_QUEUE = queue.Queue()
    result = {"code": 0}

    def worker() -> None:
        try:
            result["code"] = run_update(args)
        except Exception as exc:
            result["code"] = 2
            progress(f"Update failed: {exc}")
            if PROGRESS_QUEUE is not None:
                PROGRESS_QUEUE.put(("error", str(exc)))
        finally:
            progress_done(result["code"])

    app = UpdateWindow(args)
    threading.Thread(target=worker, daemon=True).start()
    app.mainloop()
    return result["code"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply an Auto Clear ME portable update.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--no-gui", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.no_gui:
        try:
            return run_update(args)
        except Exception as exc:
            progress(f"Update failed: {exc}")
            return 2
    return run_update_with_window(args)


if __name__ == "__main__":
    raise SystemExit(main())
