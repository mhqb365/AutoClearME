#!/usr/bin/env python3
"""
Small Windows GUI for Auto Clear ME.
"""

from __future__ import annotations

import json
import os
import re
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import tempfile
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
ENGINE_PATH = APP_DIR / "AutoClearME.py"
ICON_PATH = APP_DIR / "icon.ico"
VERSION_PATH = APP_DIR / "VERSION"
UPDATE_SCRIPT_PATH = APP_DIR / "AutoClearME_Update.py"
GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/mhqb365/AutoClearME/releases/latest"

LANGUAGE_PATH = APP_DIR / "languages.json"
FALLBACK_TEXT = {
    "en": {
        "language": "Language",
        "subtitle": "A tool to help clear ME BIOS 11 -> 20",
        "about": "About",
        "settings": "Settings",
        "bios_files": "BIOS Files",
        "single_bios": "Single BIOS",
        "dual_bios": "Dual BIOS",
        "bios_file": "BIOS file",
        "bios_file_1": "BIOS file 1",
        "bios_file_2": "BIOS file 2",
        "me_region": "ME Region",
        "fit": "FIT",
        "clear_me": "Clear ME",
        "log": "Log",
        "save_log": "Save log",
        "clear_log": "Clear log",
        "browse": "Browse...",
        "settings_title": "Auto Clear ME Settings",
        "data_sources": "Settings",
        "fit_root": "FIT root",
        "me_region_root": "ME Region root",
        "save": "Save",
        "close": "Close",
        "ready": "Ready",
        "selected_file_cleared": "Selected file cleared",
        "analyzing": "Analyzing...",
        "analyze_success": "Analyze success",
        "analyze_failed": "Analyze failed",
        "running": "Running...",
        "clear_complete": "Clear complete",
        "job_prepared": "Job prepared; FIT build required",
        "error": "Error",
        "select_input_title": "Select BIOS dump or ME region",
        "save_log_title": "Save log",
        "log_empty": "Log is empty.",
        "starting_clear": "Starting Clear ME.",
        "automatic_clear_failed": "Automatic clear did not complete.",
        "update_available_title": "Update available",
        "update_available_message": "Auto Clear ME {version} is available. Download and install it now?",
        "update_starting": "Starting update. The app will close and reopen after the update.",
        "update_check_failed": "Could not check for updates.",
    },
    "vi": {
        "language": "Ngôn ngữ",
        "subtitle": "Công cụ hỗ trợ clear ME BIOS 11 -> 20",
        "about": "Giới thiệu",
        "settings": "Cài đặt",
        "bios_files": "File BIOS",
        "single_bios": "BIOS đơn",
        "dual_bios": "BIOS kép",
        "bios_file": "File BIOS",
        "bios_file_1": "File BIOS 1",
        "bios_file_2": "File BIOS 2",
        "me_region": "ME Region",
        "fit": "FIT",
        "clear_me": "Clear ME",
        "log": "Log",
        "save_log": "Lưu log",
        "clear_log": "Xóa log",
        "browse": "Chọn...",
        "settings_title": "Cài đặt Auto Clear ME",
        "data_sources": "Cài đặt",
        "fit_root": "FIT root",
        "me_region_root": "ME Region root",
        "save": "Lưu",
        "close": "Đóng",
        "ready": "Sẵn sàng",
        "selected_file_cleared": "Đã xóa file đã chọn",
        "analyzing": "Đang phân tích...",
        "analyze_success": "Phân tích thành công",
        "analyze_failed": "Phân tích lỗi",
        "running": "Đang chạy...",
        "clear_complete": "Clear hoàn tất",
        "job_prepared": "Đã chuẩn bị job; cần build bằng FIT",
        "error": "Lỗi",
        "select_input_title": "Chọn BIOS dump hoặc ME region",
        "save_log_title": "Lưu log",
        "log_empty": "Log trống.",
        "starting_clear": "Bắt đầu Clear ME.",
        "automatic_clear_failed": "Clear tự động chưa hoàn tất.",
        "update_available_title": "Có bản cập nhật",
        "update_available_message": "Auto Clear ME {version} đã có bản mới. Tải về và cài ngay bây giờ?",
        "update_starting": "Đang bắt đầu cập nhật. App sẽ đóng và mở lại sau khi cập nhật xong.",
        "update_check_failed": "Không thể kiểm tra cập nhật.",
    }
}


def app_version() -> str:
    try:
        raw = VERSION_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        raw = "0.0.0"
    numbers = re.findall(r"\d+", raw)
    while len(numbers) < 3:
        numbers.append("0")
    return ".".join(numbers[:3])


def load_language_bundle() -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    labels = {"English": "en", "Tiếng Việt": "vi"}
    text = dict(FALLBACK_TEXT)
    try:
        data = json.loads(LANGUAGE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return text, labels
    loaded_text = data.get("text") if isinstance(data, dict) else None
    loaded_labels = data.get("labels") if isinstance(data, dict) else None
    if isinstance(loaded_text, dict) and isinstance(loaded_text.get("en"), dict):
        text = loaded_text
    if isinstance(loaded_labels, dict) and loaded_labels:
        labels = {str(label): str(code) for label, code in loaded_labels.items()}
    return text, labels


TEXT, LANG_LABELS = load_language_bundle()
LANG_NAMES = {value: key for key, value in LANG_LABELS.items()}


class ClearMeGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Auto Clear ME v{app_version()}")
        if ICON_PATH.exists():
            self.iconbitmap(str(ICON_PATH))
        self.geometry("660x620")
        self.minsize(660, 620)
        self.queue: queue.Queue[str | tuple[str, int]] = queue.Queue()
        self.last_result = ""
        self.last_analyze_result = ""
        self.analyzed_signature: tuple[str, ...] = ()
        self.analyzed_detected: dict = {}
        self.rgn_choices: dict[str, str] = {}
        self.fit_choices: dict[str, str] = {}
        self.input_paths: dict[str, str] = {}
        self.ui: dict[str, tk.Widget | str] = {}
        self.translatable_labels: list[tuple[ttk.Label, str]] = []
        self.browse_buttons: list[ttk.Button] = []
        self.lang_var = tk.StringVar(value="English")
        self.vars = {
            "csme_repo": tk.StringVar(),
            "fitc_root": tk.StringVar(),
            "input": tk.StringVar(),
            "dual_file1": tk.StringVar(),
            "dual_file2": tk.StringVar(),
            "rgn_choice": tk.StringVar(),
            "fit_choice": tk.StringVar(),
            "chip1_size": tk.StringVar(value="8MB"),
        }
        self.mode_var = tk.StringVar(value="single")
        self.control_row_height = 30
        self._build_ui()
        self.load_config()
        self.after(150, self.drain_queue)
        self.after(1200, self.check_for_updates)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        style = ttk.Style(self)
        style.configure("Control.TEntry", padding=(2, 2, 2, 2))
        style.configure("Control.TCombobox", padding=(3, 3, 3, 3))
        style.configure("Control.TButton", padding=(3, 1, 3, 1))

        header = ttk.Frame(self, padding=(18, 16, 18, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Auto Clear ME", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        self.ui["subtitle"] = ttk.Label(header)
        self.ui["subtitle"].grid(row=1, column=0, sticky="w", pady=(4, 0))
        nav = ttk.Frame(header)
        nav.grid(row=0, column=1, rowspan=2, sticky="e")
        self.ui["about_button"] = ttk.Button(nav, command=self.open_about)
        self.ui["about_button"].grid(row=0, column=0, padx=(0, 8))
        self.ui["settings_button"] = ttk.Button(nav, command=self.open_settings)
        self.ui["settings_button"].grid(row=0, column=1)

        content = ttk.Frame(self)
        content.grid(row=1, column=0, sticky="nsew", padx=18, pady=(8, 18))
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        form = ttk.LabelFrame(content, padding=14)
        self.ui["bios_files_frame"] = form
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        tabs = ttk.Notebook(form)
        tabs.grid(row=0, column=0, columnspan=4, sticky="ew")
        tabs.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        single_tab = ttk.Frame(tabs, padding=10)
        single_tab.columnconfigure(1, weight=1)
        self.path_row(single_tab, 0, "bios_file", "input", self.pick_input, clearable=True)
        self.select_row(single_tab, 1, "me_region", "rgn_choice")
        self.select_row(single_tab, 2, "fit", "fit_choice")
        tabs.add(single_tab, text="")

        dual_tab = ttk.Frame(tabs, padding=10)
        dual_tab.columnconfigure(1, weight=1)
        self.path_row(dual_tab, 0, "bios_file_1", "dual_file1", self.pick_input, clearable=True)
        self.path_row(dual_tab, 1, "bios_file_2", "dual_file2", self.pick_input, clearable=True)
        self.select_row(dual_tab, 2, "me_region", "rgn_choice")
        self.select_row(dual_tab, 3, "fit", "fit_choice")
        tabs.add(dual_tab, text="")
        self.tabs = tabs

        actions = ttk.Frame(form)
        actions.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        actions.columnconfigure(2, weight=1)
        self.clear_button = ttk.Button(actions, command=self.start_clear)
        self.clear_button.grid(row=0, column=4, padx=(8, 0))
        self.status_var = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.status_var).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))

        log_frame = ttk.LabelFrame(content, padding=10)
        self.ui["log_frame"] = log_frame
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        log_frame.rowconfigure(1, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_buttons = ttk.Frame(log_frame)
        log_buttons.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        log_buttons.columnconfigure(0, weight=1)
        self.ui["save_log_button"] = ttk.Button(log_buttons, command=self.save_log)
        self.ui["save_log_button"].grid(row=0, column=1, padx=(8, 0))
        self.ui["clear_log_button"] = ttk.Button(log_buttons, command=self.clear_log)
        self.ui["clear_log_button"].grid(row=0, column=2, padx=(8, 0))
        self.log = tk.Text(log_frame, wrap="word", height=18, font=("Consolas", 10))
        self.log.grid(row=1, column=0, sticky="nsew")
        self.log.bind("<Control-c>", self.copy_log_selection)
        self.log.bind("<Control-C>", self.copy_log_selection)
        self.log.bind("<Control-a>", self.select_all_log)
        self.log.bind("<Control-A>", self.select_all_log)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        self.content = content
        self.apply_language()

    def path_row(self, parent: ttk.Frame, row: int, label_key: str, key: str, picker, clearable: bool = False) -> None:
        parent.rowconfigure(row, minsize=self.control_row_height)
        label = ttk.Label(parent)
        label.grid(row=row, column=0, sticky="w", pady=4)
        self.translatable_labels.append((label, label_key))
        ttk.Entry(parent, textvariable=self.vars[key], style="Control.TEntry").grid(row=row, column=1, sticky="ew", padx=8, pady=3)
        browse = ttk.Button(parent, command=lambda: picker(key), style="Control.TButton")
        browse.grid(row=row, column=2, sticky="ew", pady=3)
        self.browse_buttons.append(browse)
        if clearable:
            ttk.Button(parent, text="X", width=3, command=lambda: self.clear_path(key), style="Control.TButton").grid(
                row=row, column=3, sticky="ew", padx=(6, 0), pady=3
            )

    def select_row(self, parent: ttk.Frame, row: int, label_key: str, key: str) -> None:
        parent.rowconfigure(row, minsize=self.control_row_height)
        label = ttk.Label(parent)
        label.grid(row=row, column=0, sticky="w", pady=4)
        self.translatable_labels.append((label, label_key))
        combo = ttk.Combobox(parent, textvariable=self.vars[key], state="readonly", style="Control.TCombobox")
        combo.grid(row=row, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=3)
        if key == "rgn_choice":
            self.single_rgn_combo = combo if "single_rgn_combo" not in self.__dict__ else self.single_rgn_combo
            self.dual_rgn_combo = combo
        else:
            self.single_fit_combo = combo if "single_fit_combo" not in self.__dict__ else self.single_fit_combo
            self.dual_fit_combo = combo

    def on_tab_changed(self, _event=None) -> None:
        self.mode_var.set("dual" if self.tabs.index("current") == 1 else "single")
        self.reset_analysis()
        self.status_var.set(self.t("ready"))
        self.start_analyze_selected()

    def t(self, key: str) -> str:
        lang = LANG_LABELS.get(self.lang_var.get(), "en")
        return TEXT.get(lang, TEXT["en"]).get(key, TEXT["en"].get(key, key))

    def on_language_changed(self, _event=None) -> None:
        self.apply_language()
        self.save_config(silent=True)

    def apply_language(self) -> None:
        self.ui["subtitle"].configure(text=self.t("subtitle"))
        self.ui["about_button"].configure(text=self.t("about"))
        self.ui["settings_button"].configure(text=self.t("settings"))
        self.ui["bios_files_frame"].configure(text=self.t("bios_files"))
        self.tabs.tab(0, text=self.t("single_bios"))
        self.tabs.tab(1, text=self.t("dual_bios"))
        self.clear_button.configure(text=self.t("clear_me"))
        self.ui["log_frame"].configure(text=self.t("log"))
        self.ui["save_log_button"].configure(text=self.t("save_log"))
        self.ui["clear_log_button"].configure(text=self.t("clear_log"))
        for label, label_key in self.translatable_labels:
            if label.winfo_exists():
                label.configure(text=self.t(label_key))
        for browse in self.browse_buttons:
            if browse.winfo_exists():
                browse.configure(text=self.t("browse"))
        if not self.status_var.get():
            self.status_var.set(self.t("ready"))

    def open_settings(self) -> None:
        win = tk.Toplevel(self)
        win.title(self.t("settings_title"))
        win.transient(self)
        win.grab_set()
        win.geometry("560x200")
        win.minsize(560, 200)
        frame = ttk.LabelFrame(win, text=self.t("data_sources"), padding=14)
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=self.t("language")).grid(row=0, column=0, sticky="w", pady=4)
        lang_combo = ttk.Combobox(frame, textvariable=self.lang_var, values=list(LANG_LABELS), state="readonly", width=18)
        lang_combo.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        lang_combo.bind("<<ComboboxSelected>>", self.on_language_changed)
        self.settings_path_row(frame, 1, self.t("fit_root"), "fitc_root")
        self.settings_path_row(frame, 2, self.t("me_region_root"), "csme_repo")
        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        buttons.columnconfigure(0, weight=1)
        ttk.Button(buttons, text=self.t("save"), command=lambda: (self.save_config(), win.destroy())).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text=self.t("close"), command=win.destroy).grid(row=0, column=2, padx=(8, 0))

    def settings_path_row(self, parent: ttk.Frame, row: int, label: str, key: str) -> None:
        parent.rowconfigure(row, minsize=self.control_row_height)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=self.vars[key], style="Control.TEntry").grid(row=row, column=1, sticky="ew", padx=8, pady=3)
        ttk.Button(parent, text=self.t("browse"), command=lambda: self.pick_folder(key), style="Control.TButton").grid(row=row, column=2, sticky="ew", pady=3)

    def current_version(self) -> str:
        return app_version()

    def check_for_updates(self) -> None:
        threading.Thread(target=self.fetch_latest_release, daemon=True).start()

    def fetch_latest_release(self) -> None:
        try:
            request = urllib.request.Request(GITHUB_LATEST_RELEASE_API, headers={"User-Agent": "AutoClearME"})
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            latest = str(payload.get("tag_name") or payload.get("name") or "").strip()
            url = self.release_zip_url(payload)
            if latest and url and self.version_newer(latest, self.current_version()):
                self.queue.put(("UPDATE_AVAILABLE", {"version": latest, "url": url}))
        except Exception as exc:
            self.queue.put(("UPDATE_CHECK_FAILED", str(exc)))

    def release_zip_url(self, payload: dict) -> str:
        assets = payload.get("assets") if isinstance(payload, dict) else []
        zip_assets = [
            item for item in assets
            if isinstance(item, dict)
            and str(item.get("name", "")).lower().endswith(".zip")
            and item.get("browser_download_url")
        ]
        preferred = [item for item in zip_assets if "autoclearme" in str(item.get("name", "")).lower()]
        if preferred:
            return str(preferred[0]["browser_download_url"])
        if zip_assets:
            return str(zip_assets[0]["browser_download_url"])
        return ""

    def version_newer(self, latest: str, current: str) -> bool:
        def parts(value: str) -> tuple[int, ...]:
            numbers = re.findall(r"\d+", value)
            return tuple(int(n) for n in numbers[:4]) or (0,)

        return parts(latest) > parts(current)

    def prompt_update(self, info: dict) -> None:
        version = info.get("version", "")
        url = info.get("url", "")
        if not version or not url:
            return
        message = self.t("update_available_message").format(version=version)
        if not messagebox.askyesno(self.t("update_available_title"), message, parent=self):
            return
        self.log_info(self.t("update_starting"))
        self.start_update(url)

    def start_update(self, url: str) -> None:
        if not UPDATE_SCRIPT_PATH.exists():
            self.log_error("Updater script was not found.")
            return
        update_dir = Path(tempfile.mkdtemp(prefix="AutoClearME_Update_Launcher_"))
        update_script = update_dir / UPDATE_SCRIPT_PATH.name
        shutil.copy2(UPDATE_SCRIPT_PATH, update_script)
        cmd = [
            sys.executable,
            str(update_script),
            "--url",
            url,
            "--app-dir",
            str(APP_DIR),
            "--parent-pid",
            str(os.getpid()),
        ]
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(cmd, cwd=str(update_dir), startupinfo=startupinfo, creationflags=creationflags)
        self.after(300, self.destroy)

    def open_about(self) -> None:
        webbrowser.open("https://github.com/mhqb365/AutoClearME")

    def pick_folder(self, key: str) -> None:
        initial = self.vars[key].get() or str(Path.home())
        path = filedialog.askdirectory(initialdir=initial)
        if path:
            self.vars[key].set(path)

    def pick_input(self, key: str) -> None:
        path = filedialog.askopenfilename(
            title=self.t("select_input_title"),
            filetypes=[("Firmware images", "*.bin *.rom *.fd *.cap *.bio"), ("All files", "*.*")],
        )
        if path:
            self.input_paths[key] = path
            self.vars[key].set(Path(path).name)
            self.start_analyze_selected()

    def clear_path(self, key: str) -> None:
        self.vars[key].set("")
        self.input_paths.pop(key, None)
        self.reset_analysis()
        self.status_var.set(self.t("selected_file_cleared"))

    def reset_analysis(self) -> None:
        self.analyzed_signature = ()
        self.analyzed_detected = {}
        self.set_candidates([], [])

    def input_path(self, key: str) -> str:
        return self.input_paths.get(key, self.vars[key].get().strip())

    def start_analyze_selected(self) -> None:
        cmd = self.engine_cmd("analyze", "--config", str(CONFIG_PATH))
        if self.mode_var.get() == "dual":
            file1 = self.input_path("dual_file1")
            file2 = self.input_path("dual_file2")
            if not file1 or not file2:
                self.reset_analysis()
                return
            cmd.extend(["--dual-file1", file1, "--dual-file2", file2])
        else:
            input_file = self.input_path("input")
            if not input_file:
                self.reset_analysis()
                return
            cmd.extend(["--input", input_file])

        self.last_analyze_result = ""
        self.analyzed_signature = self.current_input_signature()
        self.analyzed_detected = {}
        self.set_candidates([], [])
        self.status_var.set(self.t("analyzing"))
        threading.Thread(target=self.run_command, args=(cmd, "ANALYZE_DONE"), daemon=True).start()

    def current_input_signature(self) -> tuple[str, ...]:
        if self.mode_var.get() == "dual":
            return (
                self.input_path("dual_file1"),
                self.input_path("dual_file2"),
            )
        return (self.input_path("input"),)

    def load_config(self) -> None:
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            self.log_error(f"Could not read config.json: {exc}")
            return
        for key in ("csme_repo", "fitc_root"):
            if data.get(key):
                self.vars[key].set(data[key])
        if data.get("chip1_size"):
            self.vars["chip1_size"].set(data["chip1_size"])
        if data.get("language") in LANG_NAMES:
            self.lang_var.set(LANG_NAMES[data["language"]])
        mode = data.get("mode", "single")
        self.mode_var.set(mode if mode in {"single", "dual"} else "single")
        if hasattr(self, "tabs"):
            self.tabs.select(1 if self.mode_var.get() == "dual" else 0)
        self.apply_language()

    def save_config(self, silent: bool = False) -> None:
        data = {key: self.vars[key].get().strip() for key in ("csme_repo", "fitc_root")}
        data["chip1_size"] = self.vars["chip1_size"].get().strip()
        data["mode"] = self.mode_var.get()
        data["language"] = LANG_LABELS.get(self.lang_var.get(), "en")
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if not silent:
            self.log_info(f"Saved config: {CONFIG_PATH}")

    def validate(self) -> bool:
        checks = {
            "ME Region root": self.vars["csme_repo"].get(),
            "FIT root": self.vars["fitc_root"].get(),
        }
        if self.mode_var.get() == "dual":
            checks["Dual BIOS file 1"] = self.input_path("dual_file1")
            checks["Dual BIOS file 2"] = self.input_path("dual_file2")
        else:
            checks["Single BIOS file"] = self.input_path("input")
        missing = [name for name, value in checks.items() if not value.strip()]
        if missing:
            self.log_error("Missing selection: " + ", ".join(missing))
            return False
        return True

    def start_clear(self) -> None:
        if not self.validate():
            return
        self.save_config()
        self.clear_button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_result = ""
        self.log_info(self.t("starting_clear"))
        cmd = self.engine_cmd("prepare", "--config", str(CONFIG_PATH))
        if self.mode_var.get() == "dual":
            cmd.extend([
                "--dual-file1",
                self.input_path("dual_file1"),
                "--dual-file2",
                self.input_path("dual_file2"),
                "--dual-split",
                "--chip1-size",
                self.vars["chip1_size"].get().strip(),
            ])
        else:
            cmd.extend(["--input", self.input_path("input")])
        if self.analyzed_signature == self.current_input_signature() and self.analyzed_detected:
            cmd.extend([
                "--detected-version",
                self.analyzed_detected.get("version", ""),
                "--detected-sku",
                self.analyzed_detected.get("sku", ""),
                "--detected-type",
                self.analyzed_detected.get("type", ""),
                "--detected-data-state",
                self.analyzed_detected.get("data_state", ""),
            ])
        selected_rgn = self.rgn_choices.get(self.vars["rgn_choice"].get())
        selected_fit = self.fit_choices.get(self.vars["fit_choice"].get())
        if selected_rgn:
            cmd.extend(["--rgn", selected_rgn])
        if selected_fit:
            cmd.extend(["--fitc", selected_fit])
        cmd.append("--try-fit")
        threading.Thread(target=self.run_command, args=(cmd, "DONE"), daemon=True).start()

    def run_command(self, cmd: list[str], done_tag: str) -> None:
        if done_tag != "ANALYZE_DONE":
            self.queue.put("Command: " + " ".join(f'"{x}"' if " " in x else x for x in cmd) + "\n\n")
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            cmd,
            cwd=str(APP_DIR),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            errors="replace",
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if done_tag == "ANALYZE_DONE":
                self.last_analyze_result += line
            else:
                self.last_result += line
                if self.should_show_clear_line(line):
                    self.queue.put(line)
        proc.wait()
        self.queue.put((done_tag, proc.returncode))

    def engine_cmd(self, *args: str) -> list[str]:
        return [sys.executable, str(ENGINE_PATH), *args]

    def should_show_clear_line(self, line: str) -> bool:
        return bool(re.match(r"\[[0-5]/5\]", line.strip()))

    def drain_queue(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                if isinstance(item, tuple):
                    tag = item[0]
                    value = item[1]
                    if tag == "UPDATE_AVAILABLE":
                        self.prompt_update(value if isinstance(value, dict) else {})
                        continue
                    if tag == "UPDATE_CHECK_FAILED":
                        continue
                    code = int(value)
                    if tag == "ANALYZE_DONE":
                        self.handle_analyze_done(code)
                        continue
                    self.clear_button.configure(state="normal")
                    if code == 0:
                        if '"status": "cleared"' in self.last_result:
                            self.status_var.set(self.t("clear_complete"))
                            outputs = self.cleared_outputs()
                            location = str(outputs[0].parent) if outputs else "next to the input"
                            if len(outputs) > 1:
                                self.log_info(f"Dual BIOS clear and split complete. Output files were saved to: {location}")
                            else:
                                self.log_info(f"Clear complete. Output file was saved to: {location}")
                            self.open_output_location(outputs)
                        else:
                            self.status_var.set(self.t("job_prepared"))
                            reason = self.extract_failure_reason()
                            if reason:
                                self.log_warn(self.t("automatic_clear_failed") + "\n" + reason)
                            else:
                                self.log_warn("Job prepared. Open MANUAL_STEPS.txt in the job folder and finish the FIT build.")
                    else:
                        self.status_var.set(self.t("error"))
                        self.log_error(f"Stopped with exit code {code}. See the log above.")
                else:
                    self.write_log(item)
        except queue.Empty:
            pass
        self.after(150, self.drain_queue)

    def handle_analyze_done(self, code: int) -> None:
        if code != 0:
            self.status_var.set(self.t("analyze_failed"))
            self.reset_analysis()
            details = "\n".join(self.last_analyze_result.strip().splitlines()[-6:])
            message = f"Analyze stopped with exit code {code}."
            if details:
                message += "\n" + details
            self.log_error(message)
            return
        payload = self.parse_json_payload(self.last_analyze_result)
        detected = payload.get("detected", {}) if payload else {}
        self.analyzed_detected = detected
        version = detected.get("version") or "unknown"
        sku = detected.get("sku") or "unknown"
        firmware_type = detected.get("type") or "unknown"
        data_state = detected.get("data_state") or "unknown"
        family = detected.get("family") or "unknown"
        chipset = detected.get("chipset") or "unknown"
        fit = detected.get("fit") or "unknown"
        summary = "\n".join([
            self.t("analyze_success"),
            f"  Version: {version}",
            f"  Type: {firmware_type}",
            f"  SKU: {sku}",
            f"  Family: {family}",
            f"  Chipset: {chipset}",
            f"  FIT: {fit}",
            f"  File System: {data_state}",
        ])
        self.status_var.set(self.t("analyze_success"))
        self.set_candidates(payload.get("rgn_candidates", []), payload.get("fitc_candidates", []))
        self.log_info(summary)

    def set_candidates(self, rgn_candidates: list[dict], fit_candidates: list[dict]) -> None:
        self.rgn_choices = self.build_choice_map(rgn_candidates)
        self.fit_choices = self.build_choice_map(fit_candidates)
        rgn_values = list(self.rgn_choices)
        fit_values = list(self.fit_choices)
        for combo in (self.single_rgn_combo, self.dual_rgn_combo):
            combo.configure(values=rgn_values)
        for combo in (self.single_fit_combo, self.dual_fit_combo):
            combo.configure(values=fit_values)
        self.vars["rgn_choice"].set(rgn_values[0] if rgn_values else "")
        self.vars["fit_choice"].set(fit_values[0] if fit_values else "")

    def build_choice_map(self, candidates: list[dict]) -> dict[str, str]:
        choices = {}
        for index, item in enumerate(candidates, 1):
            path = item.get("path") or ""
            if not path:
                continue
            label = item.get("label") or Path(path).name
            choices[label] = path
        return choices

    def write_log(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")

    def log_info(self, text: str) -> None:
        self.write_log(f"[INFO] {text}\n")

    def log_warn(self, text: str) -> None:
        self.write_log(self.format_tagged_log("WARN", text))

    def log_error(self, text: str) -> None:
        self.write_log(self.format_tagged_log("ERROR", text))

    def format_tagged_log(self, tag: str, text: str) -> str:
        lines = str(text).splitlines() or [""]
        output = [f"[{tag}] {lines[0]}"]
        output.extend(f"       {line}" for line in lines[1:])
        return "\n".join(output) + "\n"

    def clear_log(self) -> None:
        self.log.delete("1.0", "end")

    def save_log(self) -> None:
        content = self.log.get("1.0", "end").strip()
        if not content:
            self.log_info(self.t("log_empty"))
            return
        path = filedialog.asksaveasfilename(
            title=self.t("save_log_title"),
            defaultextension=".txt",
            initialfile="clearme_log.txt",
            filetypes=[("Text log", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(content + "\n", encoding="utf-8")
        self.log_info(f"Saved log: {path}")

    def cleared_outputs(self) -> list[Path]:
        payload = self.parse_result_payload()
        if not payload:
            return []
        outputs = [payload.get("published_output", ""), *payload.get("split_outputs", [])]
        return [Path(output) for output in outputs if output and Path(output).exists()]

    def open_output_location(self, outputs: list[Path]) -> None:
        if not outputs:
            return
        subprocess.Popen(["explorer", "/select,", str(outputs[0])])

    def parse_result_payload(self) -> dict:
        return self.parse_json_payload(self.last_result)

    def parse_json_payload(self, text: str) -> dict:
        marker = '{\n  "status":'
        pos = text.rfind(marker)
        if pos < 0:
            return {}
        try:
            payload = json.loads(text[pos:])
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def extract_failure_reason(self) -> str:
        payload = self.parse_result_payload()
        reason = payload.get("failure_reason", "")
        if reason:
            return reason
        patterns = [
            r"Error \d+: [^\n]+",
            r"ERROR\s+: [^\n]+",
            r"Details: [^\n]+",
            r"FIT version used to build the image: [^\n]+",
        ]
        hits = []
        for pattern in patterns:
            hits.extend(re.findall(pattern, self.last_result))
        deduped = []
        for hit in hits:
            clean = hit.strip()
            if clean and clean not in deduped:
                deduped.append(clean)
        return "\n".join(deduped[-5:])

    def copy_log_selection(self, _event=None) -> str:
        try:
            selected = self.log.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        self.clipboard_clear()
        self.clipboard_append(selected)
        self.update()
        return "break"

    def select_all_log(self, _event=None) -> str:
        self.log.tag_add("sel", "1.0", "end-1c")
        self.log.mark_set("insert", "1.0")
        self.log.see("insert")
        return "break"


if __name__ == "__main__":
    ClearMeGui().mainloop()
