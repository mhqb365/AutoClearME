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
import ctypes
import contextlib
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_ROOT = TkinterDnD.Tk
except ImportError:
    DND_FILES = None
    DND_ROOT = tk.Tk


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
CONFIG_PATH = APP_DIR / "config.json"
ENGINE_PATH = APP_DIR / "AutoClearME.py"
ICON_PATH = (APP_DIR / "icon.ico") if (APP_DIR / "icon.ico").exists() else RESOURCE_DIR / "icon.ico"
ABOUT_ICON_PATH = (APP_DIR / "icon.png") if (APP_DIR / "icon.png").exists() else RESOURCE_DIR / "icon.png"
WINKEY_RE = re.compile(r"\b[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}\b", re.IGNORECASE)
VERSION_PATH = (APP_DIR / "VERSION") if (APP_DIR / "VERSION").exists() else RESOURCE_DIR / "VERSION"
UPDATE_SCRIPT_PATH = APP_DIR / "AutoClearME_Update.py"
UPDATE_EXE_PATHS = [
    APP_DIR / "_internal" / "AutoClearME_Update.exe",
    APP_DIR / "Tools" / "AutoClearME_Update.exe",
    APP_DIR / "AutoClearME_Update.exe",
]
RUNTIME_PYTHON_PATH = APP_DIR / "Runtime" / "Python" / "python.exe"
GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/mhqb365/AutoClearME/releases/latest"
APP_USER_MODEL_ID = "mhqb365.AutoClearME"

LANGUAGE_PATH = (APP_DIR / "languages.json") if (APP_DIR / "languages.json").exists() else RESOURCE_DIR / "languages.json"


def version_parts(value: str) -> tuple[int, int, int, int]:
    raw = str(value or "").strip().lstrip("vV")
    numbers = re.findall(r"\d+", raw)
    if len(numbers) == 2 and len(numbers[1]) == 2 and numbers[1].startswith("0"):
        numbers = [numbers[0], numbers[1][0], numbers[1][1]]
    parts = [int(n) for n in numbers[:4]]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def format_version(value: str) -> str:
    parts = list(version_parts(value))
    while len(parts) > 3 and parts[-1] == 0:
        parts.pop()
    return ".".join(str(part) for part in parts)


def app_version() -> str:
    try:
        raw = VERSION_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        raw = "0.0.0"
    return format_version(raw)


def set_windows_app_id() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def apply_window_icon(window: tk.Tk | tk.Toplevel) -> None:
    if not ICON_PATH.exists():
        return
    try:
        window.iconbitmap(default=str(ICON_PATH))
    except tk.TclError:
        try:
            window.iconbitmap(str(ICON_PATH))
        except tk.TclError:
            pass


def load_language_bundle() -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    data = json.loads(LANGUAGE_PATH.read_text(encoding="utf-8-sig"))
    text = data["text"]
    labels = data["labels"]
    if not isinstance(text, dict) or not isinstance(text.get("en"), dict):
        raise ValueError("languages.json must contain text.en")
    if not isinstance(labels, dict) or not labels:
        raise ValueError("languages.json must contain labels")
    return text, {str(label): str(code) for label, code in labels.items()}


TEXT, LANG_LABELS = load_language_bundle()
LANG_NAMES = {value: key for key, value in LANG_LABELS.items()}


class ClearMeGui(DND_ROOT):
    def __init__(self) -> None:
        set_windows_app_id()
        super().__init__()
        self.title(f"Auto Clear ME v{app_version()}")
        apply_window_icon(self)
        self.geometry("660x620")
        self.minsize(660, 620)
        self.queue: queue.Queue[str | tuple[str, object]] = queue.Queue()
        self.current_process: subprocess.Popen | None = None
        self.task_running = False
        self.stop_requested = False
        self.last_result = ""
        self.last_analyze_result = ""
        self.last_unlock_files: list[str] = []
        self.last_oem_dmi_files: list[str] = []
        self.last_oem_dmi_vendor = ""
        self.dell_dmi_warning_shown = False
        self.dell_8fc8_warning_shown = False
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
            "merge_bios1": tk.StringVar(),
            "merge_bios2": tk.StringVar(),
            "split_bios_input": tk.StringVar(),
            "split_bios1_size": tk.StringVar(value="8MB"),
            "split_bios2_size": tk.StringVar(value="16MB"),
            "winkey_input": tk.StringVar(),
            "winkey_new_key": tk.StringVar(),
            "oem_dmi_vendor": tk.StringVar(value="Acer"),
            "oem_dmi_target": tk.StringVar(),
            "oem_dmi_package": tk.StringVar(),
            "unlock_vendor": tk.StringVar(value="Dell 8FC8/CF1B"),
            "unlock_bios_input": tk.StringVar(),
            "asus_dmi_package": tk.StringVar(),
            "asus_dmi_target": tk.StringVar(),
            "dmi_package": tk.StringVar(),
            "dmi_target": tk.StringVar(),
            "hp_dmi_package": tk.StringVar(),
            "hp_dmi_target": tk.StringVar(),
            "acer_dmi_package": tk.StringVar(),
            "acer_dmi_target": tk.StringVar(),
            "dell_dmi_package": tk.StringVar(),
            "dell_dmi_target": tk.StringVar(),
            "dell_8fc8_input": tk.StringVar(),
            "dell_pfs_input": tk.StringVar(),
            "hp_extract_input": tk.StringVar(),
            "rgn_choice": tk.StringVar(),
            "fit_choice": tk.StringVar(),
            "chip1_size": tk.StringVar(value="8MB"),
        }
        self.mode_var = tk.StringVar(value="single")
        self.control_row_height = 30
        self._build_ui()
        self.load_config()
        self.after(150, self.bring_to_front)
        self.after(150, self.drain_queue)
        self.after(1200, self.check_for_updates)

    def bring_to_front(self) -> None:
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(250, lambda: self.attributes("-topmost", False))

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        style = ttk.Style(self)
        style.configure("Control.TEntry", padding=(2, 2, 2, 2))
        style.configure("Control.TCombobox", padding=(3, 3, 3, 3))
        style.configure("Control.TButton", padding=(3, 1, 3, 1))
        style.layout("HiddenTabs.TNotebook.Tab", [])

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
        content.rowconfigure(0, weight=1)

        panes = ttk.PanedWindow(content, orient="vertical")
        panes.grid(row=0, column=0, sticky="nsew")
        self.panes = panes

        form = ttk.Frame(panes, padding=(0, 0, 0, 10))
        form.columnconfigure(1, weight=1)
        panes.add(form, weight=0)
        self.form = form
        menu_row = ttk.Frame(form)
        menu_row.grid(row=0, column=0, columnspan=4, sticky="ew")
        menu_row.columnconfigure(1, weight=1)
        self.feature_menu = tk.Menu(menu_row, tearoff=False)
        self.feature_menu_button = ttk.Menubutton(menu_row, menu=self.feature_menu, style="Control.TButton")
        self.feature_menu_button.grid(row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        self.selected_feature_var = tk.StringVar()
        ttk.Label(menu_row, textvariable=self.selected_feature_var).grid(row=0, column=1, sticky="w", pady=(0, 8))
        tabs = ttk.Notebook(form, style="HiddenTabs.TNotebook")
        tabs.grid(row=1, column=0, columnspan=4, sticky="ew")
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

        merge_tab = ttk.Frame(tabs, padding=10)
        merge_tab.columnconfigure(1, weight=1)
        self.path_row(merge_tab, 0, "bios_file_1", "merge_bios1", self.pick_input, clearable=True)
        self.path_row(merge_tab, 1, "bios_file_2", "merge_bios2", self.pick_input, clearable=True)
        merge_actions = ttk.Frame(merge_tab)
        merge_actions.grid(row=2, column=0, columnspan=4, sticky="e", pady=(8, 0))
        self.merge_bios_button = ttk.Button(merge_actions, command=self.start_merge_bios)
        self.merge_bios_button.grid(row=0, column=0)
        tabs.add(merge_tab, text="")

        split_tab = ttk.Frame(tabs, padding=10)
        split_tab.columnconfigure(1, weight=1)
        self.path_row(split_tab, 0, "merged_bios_file", "split_bios_input", self.pick_input, clearable=True)
        self.entry_row(split_tab, 1, "bios1_size", "split_bios1_size")
        self.entry_row(split_tab, 2, "bios2_size", "split_bios2_size")
        split_actions = ttk.Frame(split_tab)
        split_actions.grid(row=3, column=0, columnspan=4, sticky="e", pady=(8, 0))
        self.split_bios_button = ttk.Button(split_actions, command=self.start_split_bios)
        self.split_bios_button.grid(row=0, column=0)
        tabs.add(split_tab, text="")

        winkey_tab = ttk.Frame(tabs, padding=10)
        winkey_tab.columnconfigure(1, weight=1)
        self.path_row(winkey_tab, 0, "bios_file", "winkey_input", self.pick_input, clearable=True)
        self.entry_row(winkey_tab, 1, "winkey", "winkey_new_key")
        winkey_actions = ttk.Frame(winkey_tab)
        winkey_actions.grid(row=2, column=0, columnspan=4, sticky="e", pady=(8, 0))
        self.winkey_find_button = ttk.Button(winkey_actions, command=self.start_find_winkey)
        self.winkey_find_button.grid(row=0, column=0, padx=(0, 8))
        self.winkey_patch_button = ttk.Button(winkey_actions, command=self.start_patch_winkey)
        self.winkey_patch_button.grid(row=0, column=1)
        self.winkey_button = self.winkey_find_button
        tabs.add(winkey_tab, text="")

        acer_dmi_tab = self.build_dmi_import_tab(tabs, "acer_dmi_target", "acer_dmi_package", self.start_import_acer_dmi, self.start_find_acer_dmi)
        self.import_acer_dmi_button = acer_dmi_tab.import_button
        self.acer_dmi_button = acer_dmi_tab.find_button
        tabs.add(acer_dmi_tab, text="")

        asus_dmi_tab = self.build_dmi_import_tab(tabs, "asus_dmi_target", "asus_dmi_package", self.start_import_asus_dmi, self.start_find_asus_dmi)
        self.import_asus_dmi_button = asus_dmi_tab.import_button
        self.asus_dmi_button = asus_dmi_tab.find_button
        tabs.add(asus_dmi_tab, text="")

        dell_dmi_tab = self.build_dmi_import_tab(tabs, "dell_dmi_target", "dell_dmi_package", self.start_import_dell_dmi, self.start_find_dell_dmi)
        self.import_dell_dmi_button = dell_dmi_tab.import_button
        self.dell_dmi_button = dell_dmi_tab.find_button
        tabs.add(dell_dmi_tab, text="")

        hp_dmi_tab = self.build_dmi_import_tab(tabs, "hp_dmi_target", "hp_dmi_package", self.start_import_hp_dmi, self.start_find_hp_dmi)
        self.import_hp_dmi_button = hp_dmi_tab.import_button
        self.hp_dmi_button = hp_dmi_tab.find_button
        tabs.add(hp_dmi_tab, text="")

        lenovo_dmi_tab = self.build_dmi_import_tab(tabs, "dmi_target", "dmi_package", self.start_import_lenovo_dmi, self.start_find_lenovo_dmi)
        self.import_dmi_button = lenovo_dmi_tab.import_button
        self.lenovo_dmi_button = lenovo_dmi_tab.find_button
        tabs.add(lenovo_dmi_tab, text="")

        dell_8fc8_tab = ttk.Frame(tabs, padding=10)
        dell_8fc8_tab.columnconfigure(1, weight=1)
        self.path_row(dell_8fc8_tab, 0, "bios_file", "dell_8fc8_input", self.pick_input, clearable=True)
        dell_8fc8_actions = ttk.Frame(dell_8fc8_tab)
        dell_8fc8_actions.grid(row=1, column=0, columnspan=4, sticky="e", pady=(8, 0))
        self.dell_8fc8_button = ttk.Button(dell_8fc8_actions, command=self.start_unlock_dell_8fc8)
        self.dell_8fc8_button.grid(row=0, column=0)
        tabs.add(dell_8fc8_tab, text="")

        self.unlock_acer_tab_button = self.build_unlock_tab(tabs, "ACER", self.start_unlock_acer)
        self.unlock_asus_tab_button = self.build_unlock_tab(tabs, "ASUS", self.start_unlock_asus)
        self.unlock_hp_tab_button = self.build_unlock_tab(tabs, "HP", self.start_unlock_hp)

        dell_pfs_tab = ttk.Frame(tabs, padding=10)
        dell_pfs_tab.columnconfigure(1, weight=1)
        self.path_row(dell_pfs_tab, 0, "dell_pfs_file", "dell_pfs_input", self.pick_dell_pfs_input, clearable=True)
        dell_pfs_actions = ttk.Frame(dell_pfs_tab)
        dell_pfs_actions.grid(row=1, column=0, columnspan=4, sticky="e", pady=(8, 0))
        self.dell_pfs_button = ttk.Button(dell_pfs_actions, command=self.start_dell_pfs_extract)
        self.dell_pfs_button.grid(row=0, column=0)
        tabs.add(dell_pfs_tab, text="")

        hp_extract_tab = ttk.Frame(tabs, padding=10)
        hp_extract_tab.columnconfigure(1, weight=1)
        self.path_row(hp_extract_tab, 0, "hp_extract_file", "hp_extract_input", self.pick_hp_extract_input, clearable=True)
        hp_extract_actions = ttk.Frame(hp_extract_tab)
        hp_extract_actions.grid(row=1, column=0, columnspan=4, sticky="e", pady=(8, 0))
        self.hp_extract_button = ttk.Button(hp_extract_actions, command=self.start_hp_extract)
        self.hp_extract_button.grid(row=0, column=0)
        tabs.add(hp_extract_tab, text="")
        self.tabs = tabs
        self.rebuild_feature_menu()
        self.unlock_acer_button = self.unlock_acer_tab_button
        self.unlock_asus_button = self.unlock_asus_tab_button
        self.unlock_hp_button = self.unlock_hp_tab_button

        actions = ttk.Frame(form)
        self.main_actions = actions
        actions.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        action_buttons = ttk.Frame(actions)
        action_buttons.grid(row=0, column=0, sticky="")
        self.clear_button = ttk.Button(action_buttons, command=self.start_clear)
        self.clear_button.grid(row=0, column=0, padx=(0, 8), pady=(0, 4))
        self.status_var = tk.StringVar(value="")
        status_bar = ttk.Frame(actions)
        status_bar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        status_bar.columnconfigure(0, weight=1)
        ttk.Label(status_bar, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.ui["stop_button"] = ttk.Button(status_bar, command=self.stop_current_task, state="disabled")
        self.ui["stop_button"].grid(row=0, column=1, sticky="e")

        log_frame = ttk.Frame(panes, padding=(0, 10, 0, 0))
        self.ui["log_frame"] = log_frame
        log_frame.rowconfigure(1, weight=1)
        log_frame.columnconfigure(0, weight=1)
        panes.add(log_frame, weight=1)
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
        self.after_idle(self.update_tabs_height)

    def path_row(self, parent: ttk.Frame, row: int, label_key: str, key: str, picker, clearable: bool = False) -> None:
        parent.rowconfigure(row, minsize=self.control_row_height)
        label = ttk.Label(parent)
        label.grid(row=row, column=0, sticky="w", pady=4)
        self.translatable_labels.append((label, label_key))
        entry = ttk.Entry(parent, textvariable=self.vars[key], style="Control.TEntry")
        entry.grid(row=row, column=1, sticky="ew", padx=8, pady=3)
        self.enable_file_drop(entry, key)
        browse = ttk.Button(parent, command=lambda: picker(key), style="Control.TButton")
        browse.grid(row=row, column=2, sticky="ew", pady=3)
        self.browse_buttons.append(browse)
        if clearable:
            ttk.Button(parent, text="X", width=3, command=lambda: self.clear_path(key), style="Control.TButton").grid(
                row=row, column=3, sticky="ew", padx=(6, 0), pady=3
            )

    def entry_row(self, parent: ttk.Frame, row: int, label_key: str, key: str) -> None:
        parent.rowconfigure(row, minsize=self.control_row_height)
        label = ttk.Label(parent)
        label.grid(row=row, column=0, sticky="w", pady=4)
        self.translatable_labels.append((label, label_key))
        entry = ttk.Entry(parent, textvariable=self.vars[key], style="Control.TEntry")
        entry.grid(row=row, column=1, columnspan=3, sticky="ew", padx=8, pady=3)

    def select_values_row(self, parent: ttk.Frame, row: int, label_key: str, key: str, values: tuple[str, ...]) -> None:
        parent.rowconfigure(row, minsize=self.control_row_height)
        label = ttk.Label(parent)
        label.grid(row=row, column=0, sticky="w", pady=4)
        self.translatable_labels.append((label, label_key))
        combo = ttk.Combobox(parent, textvariable=self.vars[key], values=values, state="readonly", style="Control.TCombobox")
        combo.grid(row=row, column=1, columnspan=3, sticky="ew", padx=8, pady=3)

    def build_dmi_import_tab(self, tabs: ttk.Notebook, target_key: str, package_key: str, import_command, find_command) -> ttk.Frame:
        tab = ttk.Frame(tabs, padding=10)
        tab.columnconfigure(1, weight=1)
        self.path_row(tab, 0, "target_bios", target_key, self.pick_input, clearable=True)
        self.path_row(tab, 1, "dmi_package", package_key, self.pick_dmi_package, clearable=True)
        actions = ttk.Frame(tab)
        actions.grid(row=2, column=0, columnspan=4, sticky="e", pady=(8, 0))
        tab.find_button = ttk.Button(actions, command=find_command)
        tab.find_button.grid(row=0, column=0, padx=(0, 8))
        tab.import_button = ttk.Button(actions, command=import_command)
        tab.import_button.grid(row=0, column=1)
        return tab

    def build_unlock_tab(self, tabs: ttk.Notebook, vendor: str, command) -> ttk.Button:
        tab = ttk.Frame(tabs, padding=10)
        tab.columnconfigure(1, weight=1)
        key = f"unlock_{vendor.lower()}_input"
        if key not in self.vars:
            self.vars[key] = tk.StringVar()
        self.path_row(tab, 0, "bios_file", key, self.pick_input, clearable=True)
        actions = ttk.Frame(tab)
        actions.grid(row=1, column=0, columnspan=4, sticky="e", pady=(8, 0))
        button = ttk.Button(actions, command=command)
        button.grid(row=0, column=0)
        tabs.add(tab, text=vendor)
        return button

    def select_row(self, parent: ttk.Frame, row: int, label_key: str, key: str) -> None:
        parent.rowconfigure(row, minsize=self.control_row_height)
        label = ttk.Label(parent)
        label.grid(row=row, column=0, sticky="w", pady=4)
        self.translatable_labels.append((label, label_key))
        combo = ttk.Combobox(parent, textvariable=self.vars[key], state="readonly", style="Control.TCombobox")
        combo.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 6), pady=3)
        browse = ttk.Button(parent, command=lambda: self.pick_choice_file(key), style="Control.TButton")
        browse.grid(row=row, column=3, sticky="ew", pady=3)
        self.browse_buttons.append(browse)
        if key == "rgn_choice":
            self.single_rgn_combo = combo if "single_rgn_combo" not in self.__dict__ else self.single_rgn_combo
            self.dual_rgn_combo = combo
        else:
            self.single_fit_combo = combo if "single_fit_combo" not in self.__dict__ else self.single_fit_combo
            self.dual_fit_combo = combo

    def on_tab_changed(self, _event=None) -> None:
        if not hasattr(self, "tabs"):
            return
        tab_index = self.tabs.index("current")
        self.update_selected_feature_label()
        current_key = self.feature_tab_keys[tab_index] if 0 <= tab_index < len(self.feature_tab_keys) else ""
        if tab_index >= 2:
            self.show_main_actions(False)
            self.update_tabs_height()
            if current_key == "dell_dmi_tab" and not self.dell_dmi_warning_shown:
                self.dell_dmi_warning_shown = True
                self.log_info(self.t("dell_dmi_warning"))
            if current_key == "dell_8fc8_unlock_tab" and not self.dell_8fc8_warning_shown:
                self.dell_8fc8_warning_shown = True
                self.log_info(self.t("dell_8fc8_warning"))
            self.reset_analysis()
            self.status_var.set(self.t("ready"))
            return
        self.show_main_actions(True)
        self.update_tabs_height()
        self.mode_var.set("dual" if tab_index == 1 else "single")
        self.reset_analysis()
        self.status_var.set(self.t("ready"))
        self.start_analyze_selected()

    def show_main_actions(self, visible: bool) -> None:
        if not hasattr(self, "main_actions"):
            return
        if visible:
            self.main_actions.grid()
        else:
            self.main_actions.grid_remove()

    def update_tabs_height(self) -> None:
        if not hasattr(self, "tabs"):
            return
        current = self.tabs.nametowidget(self.tabs.select())
        current.update_idletasks()
        self.tabs.configure(height=current.winfo_reqheight())
        self.after_idle(self.fit_control_pane_height)

    def fit_control_pane_height(self) -> None:
        if not hasattr(self, "panes") or not hasattr(self, "form"):
            return
        self.form.update_idletasks()
        try:
            self.panes.sashpos(0, self.form.winfo_reqheight())
        except tk.TclError:
            pass

    def rebuild_feature_menu(self) -> None:
        self.feature_tab_keys = [
            "single_bios",
            "dual_bios",
            "merge_bios",
            "split_bios",
            "winkey_tab",
            "acer_dmi_tab",
            "asus_dmi_tab",
            "dell_dmi_tab",
            "hp_dmi_tab",
            "lenovo_dmi_tab",
            "dell_8fc8_unlock_tab",
            "unlock_acer_tab",
            "unlock_asus_tab",
            "unlock_hp_tab",
            "dell_pfs_tab",
            "hp_extract_tab",
        ]
        self.feature_menu_groups = [
            (None, [(0, "single_bios"), (1, "dual_bios"), (2, "merge_bios"), (3, "split_bios"), (4, "winkey_tab")]),
            ("acer_group", [(5, "acer_dmi_tab"), (11, "unlock_acer_tab")]),
            ("asus_group", [(6, "asus_dmi_tab"), (12, "unlock_asus_tab")]),
            ("dell_group", [(14, "dell_pfs_tab"), (7, "dell_dmi_tab"), (10, "dell_8fc8_unlock_tab")]),
            ("hp_group", [(15, "hp_extract_tab"), (8, "hp_dmi_tab"), (13, "unlock_hp_tab")]),
            ("lenovo_group", [(9, "lenovo_dmi_tab")]),
        ]
        self.feature_menu.delete(0, "end")
        self.feature_submenus: list[tuple[tk.Menu, list[tuple[int, str]]]] = []
        for group_key, items in self.feature_menu_groups:
            if group_key is None:
                for tab_index, item_key in items:
                    self.feature_menu.add_command(
                        label=self.t(item_key),
                        command=lambda selected=tab_index: self.select_feature_tab(selected),
                    )
                self.feature_menu.add_separator()
                continue
            submenu = tk.Menu(self.feature_menu, tearoff=False)
            for tab_index, item_key in items:
                submenu.add_command(
                    label=self.t(item_key),
                    command=lambda selected=tab_index: self.select_feature_tab(selected),
                )
            self.feature_menu.add_cascade(label=self.t(group_key), menu=submenu)
            self.feature_submenus.append((submenu, items))
        self.update_feature_menu_labels()
        self.update_selected_feature_label()

    def select_feature_tab(self, index: int) -> None:
        self.tabs.select(index)

    def update_feature_menu_labels(self) -> None:
        self.feature_menu_button.configure(text=self.t("menu"))
        menu_index = 0
        for group_key, items in self.feature_menu_groups:
            if group_key is None:
                for _tab_index, item_key in items:
                    self.feature_menu.entryconfigure(menu_index, label=self.t(item_key))
                    menu_index += 1
                menu_index += 1
                continue
            self.feature_menu.entryconfigure(menu_index, label=self.t(group_key))
            menu_index += 1
        for submenu, items in self.feature_submenus:
            for item_index, (_tab_index, item_key) in enumerate(items):
                submenu.entryconfigure(item_index, label=self.t(item_key))

    def update_selected_feature_label(self) -> None:
        if not hasattr(self, "feature_tab_keys"):
            return
        current = self.tabs.index("current")
        if 0 <= current < len(self.feature_tab_keys):
            self.selected_feature_var.set(self.t(self.feature_tab_keys[current]))

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
        self.update_feature_menu_labels()
        self.update_selected_feature_label()
        self.import_dmi_button.configure(text=self.t("import_dmi"))
        self.import_hp_dmi_button.configure(text=self.t("import_hp_dmi"))
        self.import_acer_dmi_button.configure(text=self.t("import_acer_dmi"))
        self.import_dell_dmi_button.configure(text=self.t("import_dell_dmi"))
        self.import_asus_dmi_button.configure(text=self.t("import_asus_dmi"))
        self.asus_dmi_button.configure(text=self.t("asus_dmi"))
        self.lenovo_dmi_button.configure(text=self.t("lenovo_dmi"))
        self.hp_dmi_button.configure(text=self.t("hp_dmi"))
        self.acer_dmi_button.configure(text=self.t("acer_dmi"))
        self.dell_dmi_button.configure(text=self.t("dell_dmi"))
        self.winkey_find_button.configure(text=self.t("find_winkey"))
        self.winkey_patch_button.configure(text=self.t("change_winkey"))
        self.unlock_asus_button.configure(text=self.t("unlock_asus"))
        self.unlock_acer_button.configure(text=self.t("unlock_acer"))
        self.unlock_hp_button.configure(text=self.t("unlock_hp"))
        self.merge_bios_button.configure(text=self.t("merge_bios"))
        self.split_bios_button.configure(text=self.t("split_bios"))
        self.dell_8fc8_button.configure(text=self.t("unlock_dell_8fc8"))
        self.unlock_acer_tab_button.configure(text=self.t("unlock_acer"))
        self.unlock_asus_tab_button.configure(text=self.t("unlock_asus"))
        self.unlock_hp_tab_button.configure(text=self.t("unlock_hp"))
        self.dell_pfs_button.configure(text=self.t("extract_dell_pfs"))
        self.hp_extract_button.configure(text=self.t("extract_hp"))
        self.clear_button.configure(text=self.t("clear_me"))
        self.ui["stop_button"].configure(text=self.t("stop"))
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
        apply_window_icon(win)
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
        self.settings_path_row(frame, 1, self.t("me_region_root"), "csme_repo")
        self.settings_path_row(frame, 2, self.t("fit_root"), "fitc_root")
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
                self.queue.put(("UPDATE_AVAILABLE", {
                    "version": format_version(latest),
                    "url": url,
                    "changelog": str(payload.get("body") or "").strip(),
                }))
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
        return version_parts(latest) > version_parts(current)

    def prompt_update(self, info: dict) -> None:
        version = info.get("version", "")
        url = info.get("url", "")
        if not version or not url:
            return
        message = self.t("update_available_message").format(version=version)
        changelog = self.format_changelog(info.get("changelog", ""))
        if changelog:
            message = f"{message}\n\n{self.t('changelog')}:\n{changelog}"
        if not self.confirm_update_dialog(message):
            return
        self.log_info(self.t("update_starting"))
        self.start_update(url, version)

    def confirm_update_dialog(self, message: str) -> bool:
        result = tk.BooleanVar(self, value=False)
        win = tk.Toplevel(self)
        win.title(self.t("update_available_title"))
        apply_window_icon(win)
        win.transient(self)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", win.destroy)

        body = ttk.Frame(win, padding=(22, 18, 18, 14))
        body.grid(row=0, column=0, sticky="nsew")
        ttk.Label(body, text=message, wraplength=360, justify="left").grid(row=0, column=0, sticky="w")

        buttons = ttk.Frame(win, padding=(0, 10, 16, 14))
        buttons.grid(row=1, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)

        def choose(value: bool) -> None:
            result.set(value)
            win.destroy()

        yes = ttk.Button(buttons, text="Yes", command=lambda: choose(True), width=10)
        yes.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(buttons, text="No", command=lambda: choose(False), width=10).grid(row=0, column=2)
        yes.focus_set()

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.grab_set()
        self.wait_window(win)
        return bool(result.get())

    def format_changelog(self, value: object) -> str:
        lines = [line.strip() for line in str(value or "").replace("\r\n", "\n").splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            return ""
        text = "\n".join(lines)
        if len(text) > 1200:
            return text[:1200].rstrip() + "\n..."
        return text

    def start_update(self, url: str, version: str) -> None:
        update_exe = next((path for path in UPDATE_EXE_PATHS if path.exists()), None)
        if getattr(sys, "frozen", False) and update_exe is not None:
            update_dir = Path(tempfile.mkdtemp(prefix="AutoClearME_Update_Launcher_"))
            updater = update_dir / update_exe.name
            shutil.copy2(update_exe, updater)
            cmd = [
                str(updater),
                "--url",
                url,
                "--expected-version",
                version,
                "--app-dir",
                str(APP_DIR),
                "--parent-pid",
                str(os.getpid()),
            ]
            subprocess.Popen(cmd, cwd=str(update_dir), **self.hidden_process_kwargs())
            self.after(300, self.destroy)
            return

        if not UPDATE_SCRIPT_PATH.exists():
            self.log_error("Updater script was not found.")
            return
        update_dir = Path(tempfile.mkdtemp(prefix="AutoClearME_Update_Launcher_"))
        update_script = update_dir / UPDATE_SCRIPT_PATH.name
        shutil.copy2(UPDATE_SCRIPT_PATH, update_script)
        updater_python = self.prepare_update_python(update_dir)
        cmd = [
            updater_python,
            str(update_script),
            "--url",
            url,
            "--expected-version",
            version,
            "--app-dir",
            str(APP_DIR),
            "--parent-pid",
            str(os.getpid()),
        ]
        subprocess.Popen(cmd, cwd=str(update_dir), **self.hidden_process_kwargs())
        self.after(300, self.destroy)

    def prepare_update_python(self, update_dir: Path) -> str:
        runtime_root = APP_DIR / "Runtime" / "Python"
        if runtime_root.exists():
            temp_runtime = update_dir / "Runtime" / "Python"
            shutil.copytree(runtime_root, temp_runtime, dirs_exist_ok=True)
            python = temp_runtime / "python.exe"
            if python.exists():
                return str(python)
        return self.console_python()

    def open_about(self) -> None:
        win = tk.Toplevel(self)
        apply_window_icon(win)
        win.title(self.t("about"))
        win.transient(self)
        win.resizable(False, False)

        frame = ttk.Frame(win, padding=20)
        frame.grid(row=0, column=0, sticky="nsew")

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        logo_frame = ttk.Frame(top, width=70, height=96)
        logo_frame.grid(row=0, column=0, sticky="ns", padx=(0, 20))
        logo_frame.grid_propagate(False)
        logo_frame.rowconfigure(0, weight=1)
        logo_frame.rowconfigure(2, weight=1)

        if ABOUT_ICON_PATH.exists():
            try:
                logo = tk.PhotoImage(file=str(ABOUT_ICON_PATH), master=win)
                if logo.width() > 56 or logo.height() > 56:
                    logo = logo.subsample(max(1, logo.width() // 56), max(1, logo.height() // 56))
                self.about_logo = logo
                ttk.Label(logo_frame, image=logo).grid(row=1, column=0)
            except tk.TclError:
                ttk.Label(logo_frame, text="ACM", font=("Segoe UI", 16, "bold")).grid(row=1, column=0)
        else:
            ttk.Label(logo_frame, text="ACM", font=("Segoe UI", 16, "bold")).grid(row=1, column=0)

        text_frame = ttk.Frame(top)
        text_frame.grid(row=0, column=1, sticky="w")
        ttk.Label(text_frame, text=f"Auto Clear ME v{app_version()}", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            text_frame,
            text="A tool to help Clear ME BIOS and more!",
            wraplength=330,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(
            text_frame,
            text="Supports Clear ME/CSME/TXE, DMI, Win Key, BIOS Unlock, Merge/Split BIOS and Vendor Extract",
            wraplength=330,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(12, 0))

        separator = ttk.Separator(frame)
        separator.grid(row=1, column=0, sticky="ew", pady=(18, 12))
        ttk.Label(frame, text="© 2026 mhqb365 · Open Source · MIT License").grid(row=2, column=0, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Source Code", command=lambda: webbrowser.open("https://github.com/mhqb365/AutoClearME")).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Author", command=lambda: webbrowser.open("https://mhqb365.com")).grid(row=0, column=1)

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.grab_set()

    def pick_folder(self, key: str) -> None:
        initial = self.vars[key].get() or str(Path.home())
        path = filedialog.askdirectory(initialdir=initial)
        if path:
            self.vars[key].set(path)

    def pick_folder_path(self, key: str) -> None:
        initial = self.input_path(key) or str(Path.home())
        path = filedialog.askdirectory(initialdir=initial)
        if path:
            self.input_paths[key] = path
            self.vars[key].set(path)

    def pick_input(self, key: str) -> None:
        path = filedialog.askopenfilename(
            title=self.t("select_input_title"),
            filetypes=[("Firmware images", "*.bin *.rom *.fd *.cap *.bio"), ("All files", "*.*")],
        )
        if path:
            self.input_paths[key] = path
            self.vars[key].set(Path(path).name)
            if key in {"input", "dual_file1", "dual_file2"}:
                self.start_analyze_selected()

    def enable_file_drop(self, entry: ttk.Entry, key: str) -> None:
        if DND_FILES is None:
            return
        entry.drop_target_register(DND_FILES)
        entry.dnd_bind("<<Drop>>", lambda event: self.handle_file_drop(event, key))

    def handle_file_drop(self, event, key: str) -> str:
        paths = self.tk.splitlist(event.data)
        path = next((Path(value) for value in paths if Path(value).is_file()), None)
        if path is None:
            return getattr(event, "action", "copy")
        resolved = str(path.resolve())
        self.input_paths[key] = resolved
        self.vars[key].set(path.name)
        if key in {"input", "dual_file1", "dual_file2"}:
            self.start_analyze_selected()
        return getattr(event, "action", "copy")

    def pick_dell_pfs_input(self, key: str) -> None:
        path = filedialog.askopenfilename(
            title=self.t("dell_pfs_file"),
            filetypes=[("Dell update file", "*.exe *.bin *.pfs *.pkg *.cab *.rcv *.txt"), ("All files", "*.*")],
        )
        if path:
            self.input_paths[key] = path
            self.vars[key].set(Path(path).name)

    def pick_hp_extract_input(self, key: str) -> None:
        path = filedialog.askopenfilename(
            title=self.t("hp_extract_file"),
            filetypes=[("HP BIOS update file", "*.exe *.bin *.fd *.rom"), ("All files", "*.*")],
        )
        if path:
            self.input_paths[key] = path
            self.vars[key].set(Path(path).name)

    def pick_dmi_package(self, key: str) -> None:
        path = filedialog.askopenfilename(
            title=self.t("dmi_package"),
            filetypes=[("DMI package", "*.lendmi *.hpdmi *.acerdmi *.asusdmi *.delldmi"), ("All files", "*.*")],
        )
        if path:
            self.input_paths[key] = path
            self.vars[key].set(Path(path).name)

    def pick_choice_file(self, key: str) -> None:
        if key == "rgn_choice":
            initial = self.vars["csme_repo"].get() or str(Path.home())
            title = self.t("me_region")
            filetypes = [("ME Region", "*.bin *.rgn"), ("All files", "*.*")]
            choices = self.rgn_choices
            combos = (self.single_rgn_combo, self.dual_rgn_combo)
        else:
            initial = self.vars["fitc_root"].get() or str(Path.home())
            title = self.t("fit")
            filetypes = [("FIT executable", "*.exe"), ("All files", "*.*")]
            choices = self.fit_choices
            combos = (self.single_fit_combo, self.dual_fit_combo)
        path = filedialog.askopenfilename(title=title, initialdir=initial, filetypes=filetypes)
        if not path:
            return
        label = Path(path).name
        choices[label] = path
        values = [label, *[value for value in choices if value != label]]
        for combo in combos:
            combo.configure(values=values)
        self.vars[key].set(label)

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
        self.start_command(cmd, "ANALYZE_DONE")

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
        self.mode_var.set("single")
        if hasattr(self, "tabs"):
            self.tabs.select(0)
            self.show_main_actions(True)
            self.update_tabs_height()
        self.apply_language()

    def save_config(self, silent: bool = False) -> None:
        data = {key: self.vars[key].get().strip() for key in ("csme_repo", "fitc_root")}
        data["chip1_size"] = self.vars["chip1_size"].get().strip()
        data["mode"] = self.mode_var.get()
        data["language"] = LANG_LABELS.get(self.lang_var.get(), "en")
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if not silent:
            self.log_info(f"Saved config: {CONFIG_PATH.name}")

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

    def selected_bios_files(self) -> list[str]:
        if self.mode_var.get() == "dual":
            return [
                value for value in (
                    self.input_path("dual_file1"),
                    self.input_path("dual_file2"),
                )
                if value.strip()
            ]
        value = self.input_path("input")
        return [value] if value.strip() else []

    def start_find_winkey(self) -> None:
        source = self.input_path("winkey_input")
        if not source:
            self.log_info("Find Win Key skipped: please select BIOS file first")
            return
        self.last_result = ""
        files = [source]
        self.start_find_info("Win Key", "winkey", self.winkey_find_button, "WINKEY_DONE", files)

    def start_patch_winkey(self) -> None:
        source = self.input_path("winkey_input")
        key = self.vars["winkey_new_key"].get().strip().upper()
        if not source or not key:
            self.log_info("Change Win Key skipped: please select BIOS file and enter Win Key")
            return
        if not self.is_valid_winkey_input(key):
            self.log_error("Invalid Win Key format. Use XXXXX-XXXXX-XXXXX-XXXXX-XXXXX")
            return
        self.vars["winkey_new_key"].set(key)
        self.winkey_patch_button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_result = ""
        cmd = self.engine_cmd("winkey-patch", "--input", source, "--key", key)
        self.start_command(cmd, "WINKEY_PATCH_DONE")

    def is_valid_winkey_input(self, key: str) -> bool:
        compact = key.replace("-", "")
        return (
            bool(WINKEY_RE.fullmatch(key))
            and any(ch.isalpha() for ch in compact)
            and any(ch.isdigit() for ch in compact)
            and len(set(compact)) >= 6
        )

    def start_merge_bios(self) -> None:
        file1 = self.input_path("merge_bios1")
        file2 = self.input_path("merge_bios2")
        if not file1 or not file2:
            self.log_info("Merge BIOS skipped: please select BIOS 1 and BIOS 2")
            return
        self.merge_bios_button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_result = ""
        cmd = self.engine_cmd("merge-bios", "--file1", file1, "--file2", file2)
        self.start_command(cmd, "BIOS_TOOL_DONE")

    def start_split_bios(self) -> None:
        source = self.input_path("split_bios_input")
        bios1_size = self.vars["split_bios1_size"].get().strip()
        bios2_size = self.vars["split_bios2_size"].get().strip()
        if not source or not bios1_size or not bios2_size:
            self.log_info("Split BIOS skipped: please select merged BIOS and enter BIOS 1/2 sizes")
            return
        self.split_bios_button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_result = ""
        cmd = self.engine_cmd("split-bios", "--input", source, "--bios1-size", bios1_size, "--bios2-size", bios2_size)
        self.start_command(cmd, "BIOS_TOOL_DONE")

    def start_find_lenovo_dmi(self) -> None:
        self.start_find_oem_dmi("Lenovo", "lenovo-dmi", "dmi_target", self.lenovo_dmi_button, "LENOVO_DMI_DONE")

    def start_find_asus_dmi(self) -> None:
        self.start_find_oem_dmi("ASUS", "asus-dmi", "asus_dmi_target", self.asus_dmi_button, "ASUS_DMI_DONE")

    def start_find_hp_dmi(self) -> None:
        self.start_find_oem_dmi("HP", "hp-dmi", "hp_dmi_target", self.hp_dmi_button, "HP_DMI_DONE")

    def start_find_acer_dmi(self) -> None:
        self.start_find_oem_dmi("Acer", "acer-dmi", "acer_dmi_target", self.acer_dmi_button, "ACER_DMI_DONE")

    def start_find_dell_dmi(self) -> None:
        self.start_find_oem_dmi("Dell", "dell-dmi", "dell_dmi_target", self.dell_dmi_button, "DELL_DMI_DONE")

    def selected_dmi_config(self) -> tuple[str, str, str, str]:
        configs = {
            "Acer": ("Acer", "acer-dmi", "ACER_DMI_DONE", "acer-dmi-import"),
            "Asus": ("ASUS", "asus-dmi", "ASUS_DMI_DONE", "asus-dmi-import"),
            "Dell": ("Dell", "dell-dmi", "DELL_DMI_DONE", "dell-dmi-import"),
            "HP": ("HP", "hp-dmi", "HP_DMI_DONE", "hp-dmi-import"),
            "Lenovo": ("Lenovo", "lenovo-dmi", "LENOVO_DMI_DONE", "lenovo-dmi-import"),
        }
        return configs.get(self.vars["oem_dmi_vendor"].get().strip(), configs["Acer"])

    def start_find_selected_dmi(self) -> None:
        vendor, command, done_tag, _import_command = self.selected_dmi_config()
        if vendor == "Dell" and not self.dell_dmi_warning_shown:
            self.dell_dmi_warning_shown = True
            self.log_info(self.t("dell_dmi_warning"))
        self.start_find_oem_dmi(vendor, command, "oem_dmi_target", self.oem_dmi_button, done_tag)

    def start_import_selected_dmi(self) -> None:
        vendor, _command, _done_tag, import_command = self.selected_dmi_config()
        self.start_import_dmi(vendor, "oem_dmi_package", "oem_dmi_target", self.import_oem_dmi_button, import_command)

    def start_dell_pfs_extract(self) -> None:
        source = self.input_path("dell_pfs_input")
        if not source:
            self.log_info("Extract Dell skipped: please select file first")
            return
        self.dell_pfs_button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_result = ""
        cmd = self.engine_cmd("dell-extract", "--input", source)
        self.start_command(cmd, "DELL_PFS_DONE")

    def start_hp_extract(self) -> None:
        source = self.input_path("hp_extract_input")
        if not source:
            self.log_info("Extract HP skipped: please select file first")
            return
        self.hp_extract_button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_result = ""
        cmd = self.engine_cmd("hp-extract", "--input", source)
        self.start_command(cmd, "HP_EXTRACT_DONE")

    def start_find_oem_dmi(self, vendor: str, command: str, target_key: str, button: ttk.Button, done_tag: str) -> None:
        self.last_dmi_transfer_result = ""
        target = self.input_path(target_key)
        self.last_oem_dmi_files = [target] if target.strip() else []
        self.last_oem_dmi_vendor = vendor
        self.start_find_info(f"{vendor} DMI", command, button, done_tag, self.last_oem_dmi_files)

    def start_import_lenovo_dmi(self) -> None:
        self.start_import_dmi("Lenovo", "dmi_package", "dmi_target", self.import_dmi_button)

    def start_import_asus_dmi(self) -> None:
        self.start_import_dmi("ASUS", "asus_dmi_package", "asus_dmi_target", self.import_asus_dmi_button)

    def start_import_hp_dmi(self) -> None:
        self.start_import_dmi("HP", "hp_dmi_package", "hp_dmi_target", self.import_hp_dmi_button)

    def start_import_acer_dmi(self) -> None:
        self.start_import_dmi("Acer", "acer_dmi_package", "acer_dmi_target", self.import_acer_dmi_button)

    def start_import_dell_dmi(self) -> None:
        self.start_import_dmi("Dell", "dell_dmi_package", "dell_dmi_target", self.import_dell_dmi_button)

    def start_import_dmi(self, vendor: str, package_key: str, target_key: str, button: ttk.Button, command: str | None = None) -> None:
        package = self.input_path(package_key)
        target = self.input_path(target_key)
        missing = []
        if not package:
            missing.append(self.t("dmi_package"))
        if not target:
            missing.append(self.t("target_bios"))
        if missing:
            self.log_info(f"Import {vendor} DMI skipped: missing " + ", ".join(missing))
            return
        button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_dmi_transfer_result = ""
        self.last_oem_dmi_vendor = vendor
        command = command or {
            "ASUS": "asus-dmi-import",
            "HP": "hp-dmi-import",
            "Acer": "acer-dmi-import",
            "Dell": "dell-dmi-import",
        }.get(vendor, "lenovo-dmi-import")
        cmd = self.engine_cmd(command, "--dmi", package, "--target", target)
        self.start_command(cmd, "DMI_IMPORT_DONE")

    def start_export_checked_lenovo_dmi(self) -> None:
        source = self.first_oem_dmi_source()
        if not source:
            return
        vendor = self.last_oem_dmi_vendor or "Lenovo"
        if vendor == "ASUS":
            button = self.asus_dmi_button
            command = "asus-dmi-export"
        elif vendor == "HP":
            button = self.hp_dmi_button
            command = "hp-dmi-export"
        elif vendor == "Acer":
            button = self.acer_dmi_button
            command = "acer-dmi-export"
        elif vendor == "Dell":
            button = self.dell_dmi_button
            command = "dell-dmi-export"
        else:
            button = self.lenovo_dmi_button
            command = "lenovo-dmi-export"
        button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_dmi_transfer_result = ""
        cmd = self.engine_cmd(command, "--input", source)
        self.start_command(cmd, "DMI_EXPORT_DONE")

    def start_find_info(self, label: str, command: str, button: ttk.Button, done_tag: str, files: list[str] | None = None) -> None:
        if files is None:
            files = self.selected_bios_files()
        if not files:
            self.log_info(f"Find {label} skipped: please select file(s) first")
            return
        button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        cmd = self.engine_cmd(command)
        for path in files:
            cmd.extend(["--input", path])
        self.start_command(cmd, done_tag)

    def start_unlock_asus(self) -> None:
        self.start_unlock_vendor("ASUS", "unlock-asus", getattr(self, "unlock_asus_tab_button", self.unlock_asus_button), "UNLOCK_ASUS_DONE", "unlock_asus_input")

    def start_unlock_acer(self) -> None:
        self.start_unlock_vendor("ACER", "unlock-acer", getattr(self, "unlock_acer_tab_button", self.unlock_acer_button), "UNLOCK_ACER_DONE", "unlock_acer_input")

    def start_unlock_hp(self) -> None:
        self.start_unlock_vendor("HP", "unlock-hp", getattr(self, "unlock_hp_tab_button", self.unlock_hp_button), "UNLOCK_HP_DONE", "unlock_hp_input")

    def start_unlock_selected(self) -> None:
        source = self.input_path("unlock_bios_input")
        if not source:
            self.log_info("Unlock skipped: please select BIOS file first")
            return
        configs = {
            "Dell 8FC8": ("Dell 8FC8/CF1B", "unlock-dell-8fc8", "UNLOCK_DELL_8FC8_DONE"),
            "ACER": ("ACER", "unlock-acer", "UNLOCK_ACER_DONE"),
            "ASUS": ("ASUS", "unlock-asus", "UNLOCK_ASUS_DONE"),
            "HP": ("HP", "unlock-hp", "UNLOCK_HP_DONE"),
            "Dell 8FC8/CF1B": ("Dell 8FC8/CF1B", "unlock-dell-8fc8", "UNLOCK_DELL_8FC8_DONE"),
        }
        vendor, command, done_tag = configs.get(self.vars["unlock_vendor"].get().strip(), configs["Dell 8FC8/CF1B"])
        if vendor == "Dell 8FC8/CF1B" and not self.dell_8fc8_warning_shown:
            self.dell_8fc8_warning_shown = True
            self.log_info(self.t("dell_8fc8_warning"))
        self.unlock_selected_button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_unlock_result = ""
        self.last_unlock_files = [source]
        cmd = self.engine_cmd(command, "--input", source)
        self.start_command(cmd, done_tag)

    def start_unlock_dell_8fc8(self) -> None:
        source = self.input_path("dell_8fc8_input")
        if not source:
            self.log_info("Unlock Dell 8FC8/CF1B skipped: please select file first")
            return
        self.dell_8fc8_button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_unlock_result = ""
        self.last_unlock_files = [source]
        cmd = self.engine_cmd("unlock-dell-8fc8", "--input", source)
        self.start_command(cmd, "UNLOCK_DELL_8FC8_DONE")

    def start_unlock_vendor(self, vendor: str, command: str, button: ttk.Button, done_tag: str, input_key: str | None = None) -> None:
        if input_key:
            source = self.input_path(input_key)
            files = [source] if source.strip() else []
        else:
            files = self.selected_bios_files()
        if not files:
            self.log_info(f"Unlock {vendor} skipped: please select file(s) first")
            return
        button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_unlock_result = ""
        self.last_unlock_files = files
        cmd = self.engine_cmd(command)
        for path in files:
            cmd.extend(["--input", path])
        self.start_command(cmd, done_tag)

    def start_clear(self) -> None:
        if not self.validate():
            return
        self.save_config()
        self.clear_button.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.last_result = ""
        self.log_info(self.t("starting_clear"))
        self.start_command(self.build_clear_command(), "DONE")

    def build_clear_command(self) -> list[str]:
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
        self.append_cached_detection(cmd)
        self.append_selected_tools(cmd)
        cmd.append("--try-fit")
        return cmd

    def append_cached_detection(self, cmd: list[str]) -> None:
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

    def append_selected_tools(self, cmd: list[str]) -> None:
        selected_rgn = self.rgn_choices.get(self.vars["rgn_choice"].get())
        selected_fit = self.fit_choices.get(self.vars["fit_choice"].get())
        if selected_rgn:
            cmd.extend(["--rgn", selected_rgn])
        if selected_fit:
            cmd.extend(["--fitc", selected_fit])

    def start_command(self, cmd: list[str], done_tag: str) -> None:
        if self.task_running:
            self.log_warn(self.t("task_already_running"))
            return
        self.task_running = True
        self.stop_requested = False
        self.ui["stop_button"].configure(state="normal")
        threading.Thread(target=self.run_command, args=(cmd, done_tag), daemon=True).start()

    def stop_current_task(self) -> None:
        if not self.task_running:
            return
        self.stop_requested = True
        self.ui["stop_button"].configure(state="disabled")
        self.status_var.set(self.t("stopping"))
        proc = self.current_process
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **self.hidden_process_kwargs(),
                )
            else:
                proc.terminate()
        except OSError:
            pass

    def run_command(self, cmd: list[str], done_tag: str) -> None:
        if getattr(sys, "frozen", False) and cmd and Path(cmd[0]).resolve() == Path(sys.executable).resolve():
            self.run_embedded_command(cmd[1:], done_tag)
            return
        proc = subprocess.Popen(
            cmd,
            cwd=str(APP_DIR),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            errors="replace",
            **self.hidden_process_kwargs(),
        )
        self.current_process = proc
        if self.stop_requested and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        assert proc.stdout is not None
        for line in proc.stdout:
            self.record_command_line(done_tag, line)
        proc.wait()
        self.current_process = None
        self.queue.put(("TASK_STOPPED", done_tag) if self.stop_requested else (done_tag, proc.returncode))

    def run_embedded_command(self, args: list[str], done_tag: str) -> None:
        class QueueWriter:
            def __init__(writer_self, owner: ClearMeGui) -> None:
                writer_self.owner = owner
                writer_self.buffer = ""

            def write(writer_self, text: str) -> int:
                writer_self.buffer += text
                while "\n" in writer_self.buffer:
                    line, writer_self.buffer = writer_self.buffer.split("\n", 1)
                    writer_self.owner.record_command_line(done_tag, line + "\n")
                return len(text)

            def flush(writer_self) -> None:
                if writer_self.buffer:
                    writer_self.owner.record_command_line(done_tag, writer_self.buffer)
                    writer_self.buffer = ""

        writer = QueueWriter(self)
        code = 2
        try:
            from AutoClearME import main as engine_main

            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                code = engine_main(args)
        except SystemExit as exc:
            code = int(exc.code or 0) if isinstance(exc.code, int) else 2
        except Exception as exc:
            writer.write(f"[ERROR] {exc}\n")
            code = 2
        finally:
            writer.flush()
            self.current_process = None
            self.queue.put(("TASK_STOPPED", done_tag) if self.stop_requested else (done_tag, code))

    def record_command_line(self, done_tag: str, line: str) -> None:
        if done_tag == "ANALYZE_DONE":
            self.last_analyze_result += line
        elif done_tag == "WINKEY_DONE":
            self.last_result += line
            self.queue.put(line)
        elif done_tag in {"ASUS_DMI_DONE", "LENOVO_DMI_DONE", "HP_DMI_DONE", "ACER_DMI_DONE", "DELL_DMI_DONE", "DMI_EXPORT_DONE"}:
            self.last_dmi_transfer_result += line
            self.queue.put(line)
        elif done_tag in {"DELL_PFS_DONE", "HP_EXTRACT_DONE", "WINKEY_PATCH_DONE", "BIOS_TOOL_DONE"}:
            self.last_result += line
            self.queue.put(line)
        elif done_tag in {"UNLOCK_ASUS_DONE", "UNLOCK_ACER_DONE", "UNLOCK_HP_DONE", "UNLOCK_DELL_8FC8_DONE"}:
            self.last_unlock_result += line
            self.queue.put(line)
        elif done_tag == "DMI_IMPORT_DONE":
            self.last_dmi_transfer_result += line
            self.queue.put(line)
        else:
            self.last_result += line
            if self.should_show_clear_line(line):
                self.queue.put(line)

    def engine_cmd(self, *args: str) -> list[str]:
        if getattr(sys, "frozen", False):
            return [str(Path(sys.executable).resolve()), *args]
        python = RUNTIME_PYTHON_PATH if RUNTIME_PYTHON_PATH.exists() else Path(sys.executable)
        return [str(python), str(ENGINE_PATH), *args]

    def console_python(self) -> str:
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe":
            python = exe.with_name("python.exe")
            if python.exists():
                return str(python)
        return sys.executable

    def hidden_process_kwargs(self) -> dict:
        if os.name != "nt":
            return {}
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        return {
            "startupinfo": startupinfo,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        }

    def should_show_clear_line(self, line: str) -> bool:
        return bool(re.match(r"\[[0-5]/5\]", line.strip()))

    def drain_queue(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                if isinstance(item, tuple):
                    self.handle_queue_event(item)
                else:
                    self.write_log(item)
        except queue.Empty:
            pass
        self.after(150, self.drain_queue)

    def handle_queue_event(self, item: tuple[str, object]) -> None:
        tag, value = item
        if tag == "UPDATE_AVAILABLE":
            self.prompt_update(value if isinstance(value, dict) else {})
            return
        if tag == "UPDATE_CHECK_FAILED":
            return
        self.task_running = False
        self.ui["stop_button"].configure(state="disabled")
        if tag == "TASK_STOPPED":
            self.restore_action_buttons()
            self.status_var.set(self.t("ready"))
            self.log_warn(self.t("operation_stopped"))
            return
        code = int(value)
        if tag == "ANALYZE_DONE":
            self.handle_analyze_done(code)
            return
        if tag == "WINKEY_DONE":
            self.handle_winkey_find_done(code)
            return
        if tag == "WINKEY_PATCH_DONE":
            self.handle_winkey_patch_done(code)
            return
        if tag == "LENOVO_DMI_DONE":
            self.handle_oem_dmi_done(code, self.lenovo_dmi_button, "Lenovo")
            return
        if tag == "ASUS_DMI_DONE":
            self.handle_oem_dmi_done(code, self.asus_dmi_button, "ASUS")
            return
        if tag == "HP_DMI_DONE":
            self.handle_oem_dmi_done(code, self.hp_dmi_button, "HP")
            return
        if tag == "ACER_DMI_DONE":
            self.handle_oem_dmi_done(code, self.acer_dmi_button, "Acer")
            return
        if tag == "DELL_DMI_DONE":
            self.handle_oem_dmi_done(code, self.dell_dmi_button, "Dell")
            return
        if tag == "DMI_EXPORT_DONE":
            self.handle_dmi_export_done(code)
            return
        if tag == "UNLOCK_ASUS_DONE":
            self.handle_unlock_done(code, self.unlock_asus_button, "ASUS")
            return
        if tag == "UNLOCK_ACER_DONE":
            self.handle_unlock_done(code, self.unlock_acer_button, "ACER")
            return
        if tag == "UNLOCK_HP_DONE":
            self.handle_unlock_done(code, self.unlock_hp_button, "HP")
            return
        if tag == "UNLOCK_DELL_8FC8_DONE":
            self.handle_unlock_done(code, self.dell_8fc8_button, "Dell 8FC8/CF1B")
            return
        if tag == "DMI_IMPORT_DONE":
            self.handle_dmi_import_done(code)
            return
        if tag == "DELL_PFS_DONE":
            self.handle_dell_pfs_done(code)
            return
        if tag == "HP_EXTRACT_DONE":
            self.handle_hp_extract_done(code)
            return
        if tag == "BIOS_TOOL_DONE":
            self.handle_bios_tool_done(code)
            return
        self.handle_clear_done(code)

    def restore_action_buttons(self) -> None:
        for button in (
            self.clear_button,
            self.winkey_button,
            self.winkey_patch_button,
            self.unlock_asus_button,
            self.unlock_acer_button,
            self.unlock_hp_button,
            self.merge_bios_button,
            self.split_bios_button,
            self.dell_8fc8_button,
            self.asus_dmi_button,
            self.lenovo_dmi_button,
            self.hp_dmi_button,
            self.acer_dmi_button,
            self.dell_dmi_button,
            self.import_dmi_button,
            self.import_asus_dmi_button,
            self.import_hp_dmi_button,
            self.import_acer_dmi_button,
            self.import_dell_dmi_button,
            self.dell_pfs_button,
            self.hp_extract_button,
        ):
            button.configure(state="normal")

    def handle_find_info_done(self, code: int, button: ttk.Button, label: str) -> None:
        button.configure(state="normal")
        self.status_var.set(self.t("ready") if code == 0 else self.t("error"))
        if code != 0:
            self.log_error(f"Find {label} stopped with exit code {code}.")

    def handle_winkey_find_done(self, code: int) -> None:
        self.handle_find_info_done(code, self.winkey_button, "Win Key")
        if code != 0:
            return
        key = self.first_winkey_from_result()
        if key:
            self.vars["winkey_new_key"].set(key)

    def first_winkey_from_result(self) -> str:
        for line in self.last_result.splitlines():
            stripped = line.strip()
            if stripped.startswith("Old Windows Product Key:") or stripped.startswith("New Windows Product Key:"):
                continue
            match = WINKEY_RE.search(stripped)
            if match:
                return match.group(0).upper()
        return ""

    def handle_winkey_patch_done(self, code: int) -> None:
        self.winkey_patch_button.configure(state="normal")
        self.status_var.set(self.t("export_complete") if code == 0 else self.t("error"))
        if code != 0:
            self.log_error(f"Patch Win Key stopped with exit code {code}.")
            return
        self.open_output_location(self.output_lines_to_paths(self.last_result, [self.input_path("winkey_input")]))

    def handle_oem_dmi_done(self, code: int, button: ttk.Button, vendor: str) -> None:
        button.configure(state="normal")
        self.status_var.set(self.t("ready") if code == 0 else self.t("error"))
        if code != 0:
            self.log_error(f"Find {vendor} DMI stopped with exit code {code}.")
            return
        if not self.first_oem_dmi_source():
            return
        if messagebox.askyesno(f"{vendor} DMI", f"{vendor} DMI was found. Export DMI package now?", parent=self):
            self.start_export_checked_lenovo_dmi()

    def handle_dmi_export_done(self, code: int) -> None:
        self.lenovo_dmi_button.configure(state="normal")
        self.asus_dmi_button.configure(state="normal")
        self.hp_dmi_button.configure(state="normal")
        self.acer_dmi_button.configure(state="normal")
        self.dell_dmi_button.configure(state="normal")
        self.status_var.set(self.t("ready") if code == 0 else self.t("error"))
        if code != 0:
            return
        outputs = self.dmi_transfer_outputs()
        if outputs:
            self.status_var.set(self.t("export_complete"))
            package_key = "oem_dmi_package" if "oem_dmi_package" in self.vars else {
                "ASUS": "asus_dmi_package",
                "HP": "hp_dmi_package",
                "Acer": "acer_dmi_package",
                "Dell": "dell_dmi_package",
            }.get(self.last_oem_dmi_vendor or "", "dmi_package")
            self.input_paths[package_key] = str(outputs[0])
            self.vars[package_key].set(outputs[0].name)
            self.open_output_location(outputs)

    def handle_dmi_import_done(self, code: int) -> None:
        self.import_dmi_button.configure(state="normal")
        self.import_asus_dmi_button.configure(state="normal")
        self.import_hp_dmi_button.configure(state="normal")
        self.import_acer_dmi_button.configure(state="normal")
        self.import_dell_dmi_button.configure(state="normal")
        self.status_var.set(self.t("import_complete") if code == 0 else self.t("error"))
        if code != 0:
            self.log_error(f"Import {self.last_oem_dmi_vendor or 'OEM'} DMI stopped with exit code {code}.")
            return
        self.open_output_location(self.dmi_transfer_outputs())

    def handle_dell_pfs_done(self, code: int) -> None:
        self.dell_pfs_button.configure(state="normal")
        self.status_var.set(self.t("export_complete") if code == 0 else self.t("error"))
        if code != 0:
            self.log_error(f"Extract Dell stopped with exit code {code}.")
            return
        outputs = []
        source = Path(self.input_path("dell_pfs_input")).resolve()
        for line in self.last_result.splitlines():
            if line.strip().startswith("Output:"):
                name = line.split(":", 1)[1].strip()
                candidate = source.with_name(name)
                if candidate.exists():
                    outputs.append(candidate)
        self.open_output_location(outputs)

    def handle_hp_extract_done(self, code: int) -> None:
        self.hp_extract_button.configure(state="normal")
        self.status_var.set(self.t("export_complete") if code == 0 else self.t("error"))
        if code != 0:
            self.log_error(f"Extract HP stopped with exit code {code}.")
            return
        outputs = []
        source = Path(self.input_path("hp_extract_input")).resolve()
        for line in self.last_result.splitlines():
            if line.strip().startswith("Output:"):
                name = line.split(":", 1)[1].strip()
                candidate = source.with_name(name)
                if candidate.exists():
                    outputs.append(candidate)
        self.open_output_location(outputs)

    def handle_bios_tool_done(self, code: int) -> None:
        self.merge_bios_button.configure(state="normal")
        self.split_bios_button.configure(state="normal")
        self.status_var.set(self.t("export_complete") if code == 0 else self.t("error"))
        if code != 0:
            self.log_error(f"BIOS tool stopped with exit code {code}.")
            return
        outputs = []
        source_dirs = [
            self.input_path("merge_bios1"),
            self.input_path("merge_bios2"),
            self.input_path("split_bios_input"),
        ]
        for line in self.last_result.splitlines():
            if line.strip().startswith("Output:"):
                name = line.split(":", 1)[1].strip()
                for source in source_dirs:
                    if not source:
                        continue
                    candidate = Path(source).resolve().with_name(name)
                    if candidate.exists():
                        outputs.append(candidate)
                        break
        self.open_output_location(outputs)

    def handle_unlock_done(self, code: int, button: ttk.Button, vendor: str) -> None:
        button.configure(state="normal")
        if code != 0:
            self.status_var.set(self.t("error"))
            self.log_error(f"Unlock {vendor} stopped with exit code {code}.")
            return
        outputs = []
        for line in self.last_unlock_result.splitlines():
            if line.strip().startswith("Output:"):
                name = line.split(":", 1)[1].strip()
                for source in self.last_unlock_files or self.selected_bios_files():
                    candidate = Path(source).resolve().with_name(name)
                    if candidate.exists():
                        outputs.append(candidate)
                        break
        self.status_var.set(self.t("unlock_complete") if outputs else self.t("ready"))
        self.open_output_location(outputs)

    def handle_clear_done(self, code: int) -> None:
        self.clear_button.configure(state="normal")
        if code != 0:
            self.status_var.set(self.t("error"))
            self.log_error(f"Stopped with exit code {code}. See the log above.")
            return
        if '"status": "cleared"' in self.last_result:
            self.handle_clear_success()
            return
        self.status_var.set(self.t("job_prepared"))
        reason = self.extract_failure_reason()
        if reason:
            self.log_warn(self.t("automatic_clear_failed") + "\n" + reason)
        else:
            self.log_warn("Job prepared. Open MANUAL_STEPS.txt in the job folder and finish the FIT build.")

    def handle_clear_success(self) -> None:
        self.status_var.set(self.t("clear_complete"))
        outputs = self.cleared_outputs()
        location = outputs[0].parent.name if outputs else "input folder"
        if len(outputs) > 1:
            self.log_info(f"Dual BIOS clear and split complete. Output files were saved in: {location}")
        else:
            self.log_info(f"Clear complete. Output file was saved in: {location}")
        self.open_output_location(outputs)

    def handle_analyze_done(self, code: int) -> None:
        if code != 0:
            self.status_var.set(self.t("analyze_failed"))
            self.reset_analysis()
            details = self.short_analyze_error(self.last_analyze_result)
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
        bios_vendor = detected.get("bios_vendor") or "unknown"
        bios_version = detected.get("bios_version") or "Not detected"
        summary = "\n".join([
            self.t("analyze_success"),
            f"  BIOS Vendor: {bios_vendor}",
            f"  BIOS Version: {bios_version}",
            f"  Family: {family}",
            f"  Version: {version}",
            *([f"  Release: {detected['release']}"] if detected.get("release") else []),
            f"  Type: {firmware_type}",
            f"  SKU: {sku}",
            f"  Chipset: {chipset}",
            *([f"  Chipset Support: {detected['chipset_support']}"] if detected.get("chipset_support") else []),
            *([f"  TCB SVN: {detected['tcb_svn']}"] if detected.get("tcb_svn") else []),
            *([f"  VCN: {detected['vcn']}"] if detected.get("vcn") else []),
            *([f"  Production Ready: {detected['production_ready']}"] if detected.get("production_ready") else []),
            *([f"  Workstation Support: {detected['workstation_support']}"] if detected.get("workstation_support") else []),
            *([f"  OEM Configuration: {detected['oem_configuration']}"] if detected.get("oem_configuration") else []),
            *([f"  Date: {detected['date']}"] if detected.get("date") else []),
            *([f"  Size: {detected['size']}"] if detected.get("size") else []),
            f"  FIT: {fit}",
            f"  File System: {data_state}",
            *([f"  MEA Database Name: {detected['mea_database_name']}"] if detected.get("mea_database_name") else []),
            *([f"  MEA Support Status: {detected['mea_support_status']}"] if detected.get("mea_support_status") else []),
            *([f"  RSA Signature Hash: {detected['rsa_signature_hash']}"] if detected.get("rsa_signature_hash") else []),
        ])
        self.status_var.set(self.t("analyze_success"))
        self.set_candidates(payload.get("rgn_candidates", []), payload.get("fitc_candidates", []))
        self.log_info(summary)

    def short_analyze_error(self, output: str) -> str:
        lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
        if not lines:
            return ""
        for prefix in ("RuntimeError:", "ValueError:", "Error:"):
            for line in reversed(lines):
                if prefix in line:
                    return line.split(prefix, 1)[1].strip()
        useful = [
            line for line in lines
            if not line.startswith("File ")
            and not line.startswith("Traceback")
            and not line.startswith("^")
            and not line.startswith("return ")
            and not line.startswith("raise ")
        ]
        return useful[-1] if useful else lines[-1]

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
        output.extend(f"  {line}" if line else "" for line in lines[1:])
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
        self.log_info(f"Saved log: {Path(path).name}")

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

    def first_oem_dmi_source(self) -> str:
        vendor = self.last_oem_dmi_vendor or "Lenovo"
        result_text = getattr(self, "last_dmi_transfer_result", "")
        result_lower = result_text.lower()
        for index, source in enumerate(self.last_oem_dmi_files):
            name = Path(source).name
            marker = f"[INFO] Finding {vendor} DMI in {name}"
            start = result_lower.find(marker.lower())
            if start < 0:
                continue
            next_start = len(result_text)
            if index + 1 < len(self.last_oem_dmi_files):
                next_marker = f"[INFO] Finding {vendor} DMI in {Path(self.last_oem_dmi_files[index + 1]).name}"
                found = result_lower.find(next_marker.lower(), start + len(marker))
                if found >= 0:
                    next_start = found
            section = result_lower[start:next_start]
            if f"No {vendor} DMI found".lower() not in section:
                return source
        return ""

    def dmi_transfer_outputs(self) -> list[Path]:
        source_dirs = [
            self.input_path("dmi_target"),
            self.input_path("dmi_package"),
            self.input_path("asus_dmi_target"),
            self.input_path("asus_dmi_package"),
            self.input_path("hp_dmi_target"),
            self.input_path("hp_dmi_package"),
            self.input_path("acer_dmi_target"),
            self.input_path("acer_dmi_package"),
            self.input_path("dell_dmi_target"),
            self.input_path("dell_dmi_package"),
            *self.selected_bios_files(),
            *self.last_oem_dmi_files,
        ]
        return self.output_lines_to_paths(getattr(self, "last_dmi_transfer_result", ""), source_dirs)

    def output_lines_to_paths(self, text: str, source_dirs: list[str]) -> list[Path]:
        outputs = []
        for line in text.splitlines():
            if line.strip().startswith("Output:"):
                name = line.split(":", 1)[1].strip()
                for source in source_dirs:
                    if not source:
                        continue
                    candidate = Path(source).resolve().with_name(name)
                    if candidate.exists():
                        outputs.append(candidate)
                        break
        return outputs

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
    if getattr(sys, "frozen", False) and len(sys.argv) > 1:
        from AutoClearME import main as engine_main

        raise SystemExit(engine_main(sys.argv[1:]))
    ClearMeGui().mainloop()
