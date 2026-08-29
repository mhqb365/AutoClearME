#!/usr/bin/env python3
"""
Auto Clear ME - helper for cleaning Intel CSME 11-20 BIOS/ME dumps.

This tool automates the boring and risky parts of the Win-Raid clean ME flow:
- identify input firmware with ME Analyzer when available
- find a matching Intel RGN image from your ME Region root
- choose the matching FIT folder
- create a reproducible working folder
- optionally call FIT command-line build if your FIT version supports it

It deliberately refuses to overwrite the source image.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import datetime as _dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import importlib.util
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


RGN_RE = re.compile(
    r"(?P<version>\d+\.\d+(?:\.\d+){0,2}).*?(?P<sku>CON|COR|SLM|Consumer|Corporate|Slim|H|LP|N|1\.5MB|5MB|Ignition|SPS|CSTXE|TXE).*?PRD.*?RGN",
    re.IGNORECASE,
)
VERSION_RE = re.compile(r"(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<hotfix>\d+))?(?:\.(?P<build>\d+))?")
APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = APP_DIR / "config.json"
LOCAL_MEA = APP_DIR / "MEA" / "MEA.py"
MEA_PYTHON_PACKAGES = ("colorama", "crccheck", "pltable")
ASUS_AMITSE_MARKER = bytes.fromhex("41 4D 49 54 53 45 53 65 74 75 70 00")
ASUS_UNLOCK_ZERO_LENGTH = 80
ACER_OLD_PASSWORD_MARKER = bytes.fromhex("5F 50 53 57 5F")
ACER_OLD_PASSWORD_OFFSET = 0x10
ACER_OLD_UNLOCK_ZERO_LENGTH = 0x20
ACER_NEW_PASSWORD_MARKER = bytes.fromhex("5F 55 55 AA AA 5F")
ACER_NEW_UNLOCK_ZERO_LENGTH = 80
HP_NVRAM_ACTIVE_MARKER = b"NvramActiveRegn\x00"
HP_UNLOCK_SCAN_SIZE = 0x1000
HP_UNLOCK_REQUIRED_MARKERS = (b"H_AuthVar\x00", b"H_SmartCover\x00")
HP_UNLOCK_OPTIONAL_MARKERS = (b"H_ShrdCrInf\x00", b"H_MeFwEcSts\x00")
HP_EC_UNLOCK_REQUIRED_MARKERS = (b"H_AuthVar\x00", b"H_ShrdCrInf\x00", b"H_MeFwEcSts\x00")
DELL_DMI_BLOCK_SIZE = 0x10000
DELL_IDENTITY_BLOCK_SIZE = 0x100
DELL_DMI_MARKER = b"$DMI"
DELL_SERVICE_TAG_NAME = "EfiD01ServiceTag".encode("utf-16le") + b"\x00\x00"
DELL_EPPID_NAME = "D01EppidVar".encode("utf-16le") + b"\x00\x00"
DELL_SERVICE_TAG_PATTERN = re.compile(rb"^[A-Z0-9]{7}$")
DELL_MODEL_PATTERN = re.compile(r"^\$?(Inspiron|Vostro|Latitude|Precision|XPS|OptiPlex|Alienware)(?: [A-Za-z0-9][A-Za-z0-9 -]{1,48})?$", re.IGNORECASE)
DELL_MODEL_BYTES_PATTERN = re.compile(rb"\$?(?:Inspiron|Vostro|Latitude|Precision|XPS|OptiPlex|Alienware)(?: [A-Za-z0-9][A-Za-z0-9 -]{1,48})?", re.IGNORECASE)
DELL_8FC8_UNLOCK_SIGNATURES = (
    bytes.fromhex("00 FD AA 30 00 00 00 00 04 00 FF"),
    bytes.fromhex("00 FC AA 31 00 00 00 00 04 00 FF"),
    bytes.fromhex("00 FD AA 31 00 00 00 00 00 00 FF"),
)
DELL_8FC8_UNLOCKED_SIGNATURES = tuple(signature[:2] + b"\x00" + signature[3:] for signature in DELL_8FC8_UNLOCK_SIGNATURES)


@dataclass
class FirmwareInfo:
    version: str = ""
    major: int | None = None
    minor: int | None = None
    family: str = ""
    sku: str = ""
    type: str = ""
    chipset: str = ""
    fit: str = ""
    data_state: str = ""
    release: str = ""
    tcb_svn: str = ""
    vcn: str = ""
    production_ready: str = ""
    workstation_support: str = ""
    oem_configuration: str = ""
    date: str = ""
    size: str = ""
    chipset_support: str = ""
    mea_database_name: str = ""
    mea_support_status: str = ""
    rsa_signature_hash: str = ""
    bios_vendor: str = ""
    bios_version: str = ""
    raw: str = ""


@dataclass
class PrepareInput:
    image: Path
    out_root: Path
    source_image: Path | None = None
    temp_input_dir: Path | None = None
    temp_merged_input: Path | None = None
    merged_chip1_size: int | None = None
    dual_file1_original: Path | None = None
    dual_file2_original: Path | None = None


@dataclass
class BuildResult:
    published_output: Path | None
    split_outputs: list[str]
    fitc_runs: list[dict]
    fitc: Path | None


@dataclass
class FlashRegion:
    name: str
    offset: int
    size: int


@dataclass
class WinKeyCandidate:
    method: str
    offset: int
    key: str
    length: int = 29
    classification: str = ""


@dataclass
class LenovoDmiItem:
    label: str
    value: str
    offset: int = -1
    end: int = -1


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        errors="replace",
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    return proc.returncode, proc.stdout


def find_files(root: Path, names: Iterable[str]) -> list[Path]:
    wanted = {n.lower() for n in names}
    return [p for p in root.rglob("*") if p.is_file() and p.name.lower() in wanted]


def find_me_analyzer(search_roots: list[Path]) -> Path | None:
    if LOCAL_MEA.exists():
        return LOCAL_MEA
    names = ["MEA.exe", "ME Analyzer.exe", "MEA.py"]
    for root in search_roots:
        if root.exists():
            found = find_files(root, names)
            if found:
                return found[0]
    return None


def analyze_with_mea(mea: Path, image: Path) -> FirmwareInfo:
    if mea.suffix.lower() == ".py":
        ensure_mea_dependencies()
        cmd = [sys.executable, str(mea), "-skip", "-exit", str(image)]
    else:
        cmd = [str(mea), "-skip", "-exit", str(image)]
    code, out = run(cmd, cwd=mea.parent)
    info = parse_mea_output(out)
    info.raw = out
    if code != 0 and not info.version:
        raise RuntimeError(f"ME Analyzer failed:\n{out}")
    return info


def ensure_mea_dependencies() -> None:
    missing = [package for package in MEA_PYTHON_PACKAGES if not importlib.util.find_spec(package)]
    if not missing:
        return
    print("[MEA] Installing missing Python packages for ME Analyzer: " + ", ".join(missing), flush=True)
    code, out = run([sys.executable, "-m", "pip", "install", *missing])
    if code != 0:
        install_cmd = f"{sys.executable} -m pip install " + " ".join(missing)
        raise RuntimeError(
            "MEA.py needs extra Python packages, but automatic installation failed.\n"
            f"Run this command manually:\n{install_cmd}\n\n{out}"
        )


def format_mea_size(value: str) -> str:
    try:
        size_bytes = int(value.strip(), 0)
    except ValueError:
        return value
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def parse_mea_output(text: str) -> FirmwareInfo:
    info = FirmwareInfo(raw=text)
    for line in text.splitlines():
        clean = re.sub(r"\x1b\[[0-9;]*m", "", line)
        clean = clean.replace(chr(0x2551), "|").replace(chr(0x2502), "|").strip(" |")
        cells = [cell.strip() for cell in clean.split("|") if cell.strip()]
        if len(cells) < 2 and ":" in clean:
            key, value = clean.split(":", 1)
            cells = [key.strip(), value.strip()]
        if len(cells) >= 2:
            key = cells[0].lower()
            value = cells[-1].strip()
            if key == "family" and not info.family:
                info.family = value
            elif key == "version" and not info.version:
                vm = VERSION_RE.search(value)
                info.version = vm.group(0) if vm else value
            elif key == "type" and not info.type:
                info.type = value
            elif key == "sku" and not info.sku:
                info.sku = normalize_sku(value)
            elif key == "chipset" and not info.chipset:
                info.chipset = value
            elif key in {"fit", "flash image tool"} and not info.fit:
                info.fit = value
            elif key in {"file system", "file system state", "data state"} and not info.data_state:
                info.data_state = value
            elif key == "release" and not info.release:
                info.release = value
            elif key in {"tcb svn", "tcb s.v.n", "tcb security version number"} and not info.tcb_svn:
                info.tcb_svn = value
            elif key in {"vcn", "v.c.n", "version control number"} and not info.vcn:
                info.vcn = value
            elif key in {"production ready", "prod. ready"} and not info.production_ready:
                info.production_ready = value
            elif key in {"workstation", "workstation support"} and not info.workstation_support:
                info.workstation_support = value
            elif key in {"oem config", "oem configurable", "oem configuration"} and not info.oem_configuration:
                info.oem_configuration = value
            elif key == "date" and not info.date:
                info.date = value
            elif key == "size" and not info.size:
                info.size = format_mea_size(value)
            elif key in {"chipset support", "platform"} and not info.chipset_support:
                info.chipset_support = value
            elif key == "mea database name" and not info.mea_database_name:
                info.mea_database_name = value
            elif key == "mea support status" and not info.mea_support_status:
                info.mea_support_status = value
            elif key == "rsa signature hash" and not info.rsa_signature_hash:
                info.rsa_signature_hash = value
        lower = clean.lower()
        if "version" in lower and not info.version:
            m = VERSION_RE.search(clean)
            if m:
                info.version = m.group(0)
        if "sku" in lower and not info.sku:
            normalized = normalize_sku(clean)
            info.sku = normalized if normalized != clean.lower() else clean
        if "type" in lower and not info.type:
            if "extr" in lower or "extracted" in lower:
                info.type = "Extracted"
            elif "rgn" in lower or "region" in lower:
                info.type = "Region"
        if ("file system state" in lower or re.search(r"\bdata\b", lower)) and not info.data_state:
            if "unconfigured" in lower:
                info.data_state = "Unconfigured"
            elif "not initialized" in lower:
                info.data_state = "Configured, not Initialized"
            elif "initialized" in lower:
                info.data_state = "Initialized"
            elif "configured" in lower:
                info.data_state = "Configured"
    m = VERSION_RE.search(info.version)
    if m:
        info.major = int(m.group("major"))
        info.minor = int(m.group("minor"))
    return info


def parse_filename_info(path: Path) -> FirmwareInfo:
    info = FirmwareInfo()
    name = path.name
    vm = VERSION_RE.search(name)
    if vm:
        info.version = vm.group(0)
        info.major = int(vm.group("major"))
        info.minor = int(vm.group("minor"))
    info.sku = sku_from_filename(name)
    upper = name.upper()
    if "RGN" in upper:
        info.type = "Region"
    elif "EXTR" in upper:
        info.type = "Extracted"
    return info


def normalize_sku(value: str) -> str:
    text = re.sub(r"[^a-z0-9.]+", " ", value.lower()).strip()
    text = re.sub(r"\bcon\b", "consumer", text)
    text = re.sub(r"\bcor\b", "corporate", text)
    text = re.sub(r"\bslm\b", "slim", text)
    text = re.sub(r"\bnopdm\b|\bnpdm\b", "npdm", text)
    aliases = {
        "consumer h d": "consumer h",
        "consumer lp c": "consumer lp",
        "corporate h d": "corporate h",
        "corporate lp c": "corporate lp",
        "consumer h": "consumer h",
        "consumer lp": "consumer lp",
        "consumer n": "consumer n",
        "consumer p": "consumer p",
        "corporate h": "corporate h",
        "corporate lp": "corporate lp",
        "corporate n": "corporate n",
        "corporate p": "corporate p",
        "slim h": "slim h",
        "slim lp": "slim lp",
        "slim n": "slim n",
        "slim p": "slim p",
        "1.5mb": "1.5mb",
        "5mb": "5mb",
        "consumer": "consumer",
        "corporate": "corporate",
        "slim": "slim",
        "h": "h",
        "lp": "lp",
        "n": "n",
        "p": "p",
    }
    for key, normalized in aliases.items():
        if re.search(rf"(?<![a-z0-9.]){re.escape(key)}(?![a-z0-9.])", text):
            return normalized
    return text


def display_sku(value: str) -> str:
    normalized = normalize_sku(value)
    if not normalized:
        return ""
    replacements = {
        "lp": "LP",
        "h": "H",
        "n": "N",
        "p": "P",
        "npdm": "NPDM",
        "1.5mb": "1.5MB",
        "5mb": "5MB",
    }
    return " ".join(replacements.get(part, part.capitalize()) for part in normalized.split())


def sku_key(value: str) -> tuple[str, str]:
    normalized = normalize_sku(value)
    family = ""
    platform = ""
    if "consumer" in normalized:
        family = "consumer"
    elif "corporate" in normalized:
        family = "corporate"
    elif "slim" in normalized:
        family = "slim"
    elif "sps" in normalized:
        family = "sps"
    elif "cstxe" in normalized:
        family = "cstxe"
    elif "txe" in normalized:
        family = "txe"

    parts = set(normalized.split())
    if "lp" in parts:
        platform = "lp"
    elif "h" in parts:
        platform = "h"
    elif "n" in parts:
        platform = "n"
    elif "p" in parts:
        platform = "p"
    return family, platform


def sku_matches(input_sku: str, candidate_sku: str) -> bool:
    input_family, input_platform = sku_key(input_sku)
    candidate_family, candidate_platform = sku_key(candidate_sku)
    if not input_family or not candidate_family:
        return False
    if input_family != candidate_family:
        return False
    if input_platform and candidate_platform and input_platform != candidate_platform:
        return False
    return True


def sku_from_filename(name: str) -> str:
    tokens = [token for token in re.split(r"[^A-Z0-9]+", name.upper()) if token]
    families = {"CON": "consumer", "COR": "corporate", "SLM": "slim"}
    platforms = {"LP": "lp", "H": "h", "N": "n", "P": "p"}
    for index, token in enumerate(tokens):
        if token not in families:
            continue
        sku = families[token]
        for next_token in tokens[index + 1:index + 5]:
            if next_token in platforms:
                sku += " " + platforms[next_token]
                break
        return sku
    return ""


def version_tuple(value: str) -> tuple[int, int, int, int]:
    m = VERSION_RE.search(value or "")
    if not m:
        return (0, 0, 0, 0)
    return (
        int(m.group("major") or 0),
        int(m.group("minor") or 0),
        int(m.group("hotfix") or 0),
        int(m.group("build") or 0),
    )


def version_rank(value: str) -> int:
    major, minor, hotfix, build = version_tuple(value)
    return major * 1_000_000_000 + minor * 1_000_000 + hotfix * 10_000 + build


WINKEY_LENGTH = 29
WINKEY_PATTERN = re.compile(rb"(?<![A-Za-z0-9])[A-Za-z0-9]{5}(?:-[A-Za-z0-9]{5}){4}(?![A-Za-z0-9])")
WINKEY_OEM_MARKER = bytes([
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x1D, 0x00, 0x00, 0x00,
])
WINKEY_ANCHORS = ("Windows", "Product", "ProductKey", "DigitalProductId")
WINKEY_KNOWN_KEYS = {
    "7H3HT-N36VD-XK866-8RV8Y-39M6M": "Win 10 RTM Core OEM:DM, EULA OEM",
    "TX9XD-98N7V-6WMQ6-BX7FG-H8Q99": "Windows 10/11 Home generic install key, Retail channel",
    "VK7JG-NPHTM-C97JM-9MPGT-3V66T": "Windows 10/11 Pro generic install key, Retail channel",
    "W269N-WFGWX-YVC9B-4J6C9-T83GX": "Windows 10/11 Pro generic install key, Volume KMS client",
    "NPPR9-FWDCX-D2C8J-H872K-2YT43": "Windows 10/11 Enterprise generic install key, Volume KMS client",
    "MH37W-N47XK-V7XM9-C7227-GCQG9": "Windows 10/11 Pro N generic install key, Retail channel",
    "NW6C2-QMPVW-D7KKK-3GKT6-VCFB2": "Windows 10/11 Education generic install key, Volume KMS client",
    "2WH4N-8QGBV-H22JP-CT43Q-MDWWJ": "Windows 10/11 Education N generic install key, Volume KMS client",
}


def find_all_bytes(buffer: bytes, pattern: bytes, start: int = 0) -> Iterable[int]:
    offset = max(0, start)
    while pattern:
        found = buffer.find(pattern, offset)
        if found < 0:
            return
        yield found
        offset = found + 1


def method_priority(method: str) -> int:
    if method == "Hex marker":
        return 0
    if method == "ACPI MSDM":
        return 1
    if method == "Lenovo LENV XOR DMI":
        return 2
    if method.startswith("Near "):
        return 3
    return 4


def is_valid_winkey(key: str) -> bool:
    compact = key.replace("-", "")
    if not any(ch.isalpha() for ch in compact) or not any(ch.isdigit() for ch in compact):
        return False
    groups = key.split("-")
    if groups and all(group == groups[0] for group in groups):
        return False
    return len(set(compact)) >= 6


def classify_winkey(key: str, method: str) -> str:
    pidgen = classify_winkey_with_pidgenx(key)
    if pidgen:
        return pidgen
    known = WINKEY_KNOWN_KEYS.get(key.upper())
    if known:
        return known
    if method in {"Hex marker", "ACPI MSDM"}:
        return "likely OEM:DM embedded key"
    if method == "Lenovo LENV XOR DMI":
        return "likely Lenovo XOR-decoded DMI/OEM key"
    if "DigitalProductId" in method:
        return "likely installed Windows product key"
    if method.startswith("Near "):
        return "possible Windows product key"
    return "product key candidate"


def classify_winkey_with_pidgenx(key: str) -> str:
    if os.name != "nt":
        return ""
    try:
        windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
        pkey_config = windows / "System32" / "spp" / "tokens" / "pkeyconfig" / "pkeyconfig.xrm-ms"
        if not pkey_config.exists():
            return ""
        digital_product_id4 = (ctypes.c_ubyte * 0x04F8)()
        digital_product_id4[0] = 0xF8
        digital_product_id4[1] = 0x04
        result = ctypes.windll.pidgenx.PidGenX(
            ctypes.c_wchar_p(key),
            ctypes.c_wchar_p(str(pkey_config)),
            ctypes.c_wchar_p("00000"),
            ctypes.c_int(0),
            ctypes.c_void_p(),
            ctypes.c_void_p(),
            digital_product_id4,
        )
        if result != 0:
            return ""
        data = bytes(digital_product_id4)
        strings = read_printable_utf16_strings(data)
        edition = next((value for value in strings if is_windows_edition_string(value)), "")
        eula = next(
            (
                value
                for value in strings
                if value.lower() in {"oem", "retail", "volume"}
            ),
            "",
        )
        channel = read_utf16_string(data, 1016, 128)
        parts = []
        if edition:
            edition_label = format_windows_edition(edition)
            parts.append(f"{edition_label} {channel}".strip() if channel else edition_label)
        elif channel:
            parts.append(channel)
        if eula:
            eula_label = eula.upper() if eula.lower() == "oem" else eula.capitalize()
            parts.append("EULA " + eula_label)
        return ", ".join(dict.fromkeys(parts))
    except Exception:
        return ""


def read_printable_utf16_strings(buffer: bytes) -> list[str]:
    values = []
    current = []
    for offset in range(0, len(buffer) - 1, 2):
        value = int.from_bytes(buffer[offset:offset + 2], "little")
        if 0x20 <= value <= 0x7E:
            current.append(chr(value))
            continue
        if len(current) >= 3:
            values.append("".join(current).strip())
        current = []
    if len(current) >= 3:
        values.append("".join(current).strip())
    return list(dict.fromkeys(value for value in values if value))


def read_utf16_string(buffer: bytes, offset: int, length: int) -> str:
    if offset < 0 or length <= 0 or offset >= len(buffer):
        return ""
    value = buffer[offset:offset + length].decode("utf-16le", errors="ignore")
    return value.split("\0", 1)[0].strip()


def is_windows_edition_string(value: str) -> bool:
    if not value or "-" in value or "." in value:
        return False
    lower = value.lower()
    return any(token in lower for token in ("core", "professional", "enterprise", "education", "server"))


def format_windows_edition(value: str) -> str:
    aliases = {
        "professional": "Win 10 RTM Professional",
        "core": "Win 10 RTM Core",
        "enterprise": "Win 10 RTM Enterprise",
        "education": "Win 10 RTM Education",
    }
    return aliases.get(value.strip().lower(), value.strip())


def add_winkey_range_matches(
    buffer: bytes,
    start: int,
    length: int,
    method: str,
    by_offset: dict[int, WinKeyCandidate],
    base_offset: int = 0,
) -> None:
    if start < 0 or start >= len(buffer) or length <= 0:
        return
    end = min(len(buffer), start + length)
    for match in WINKEY_PATTERN.finditer(buffer, start, end):
        key = match.group(0).decode("ascii").upper()
        if not is_valid_winkey(key):
            continue
        original_offset = base_offset + match.start()
        existing = by_offset.get(original_offset)
        if existing and method_priority(existing.method) <= method_priority(method):
            continue
        by_offset[original_offset] = WinKeyCandidate(
            method=method,
            offset=original_offset,
            key=key,
            classification=classify_winkey(key, method),
        )


def add_lenovo_lenv_matches(buffer: bytes, by_offset: dict[int, WinKeyCandidate]) -> None:
    for block_offset in find_all_bytes(buffer, b"LENV"):
        if block_offset + 0x10 >= len(buffer):
            continue
        block_length = min(0x1000, len(buffer) - block_offset)
        body_length = block_length - 0x10
        if body_length < 0x18:
            continue
        xor_key = buffer[block_offset + 0x0D]
        body = bytes(value ^ xor_key for value in buffer[block_offset + 0x10:block_offset + 0x10 + body_length])
        entry_count = int.from_bytes(buffer[block_offset + 0x08:block_offset + 0x0C], "little", signed=False)
        found_entry = False
        entry_offset = 0
        if 0 < entry_count <= 256:
            for _entry_index in range(entry_count):
                if entry_offset + 0x18 > len(body):
                    break
                data_size = int.from_bytes(body[entry_offset + 0x10:entry_offset + 0x14], "little", signed=False)
                if data_size > len(body) - entry_offset - 0x18:
                    break
                found_entry = True
                add_winkey_range_matches(
                    body,
                    entry_offset + 0x18,
                    data_size,
                    "Lenovo LENV XOR DMI",
                    by_offset,
                    block_offset + 0x10,
                )
                entry_offset += 0x18 + data_size
        if not found_entry:
            add_winkey_range_matches(body, 0, len(body), "Lenovo LENV XOR DMI", by_offset, block_offset + 0x10)


def lenovo_lenv_bodies(buffer: bytes) -> list[tuple[bytes, int]]:
    bodies = []
    for block_offset in find_all_bytes(buffer, b"LENV"):
        if block_offset + 0x10 >= len(buffer):
            continue
        block_length = min(0x1000, len(buffer) - block_offset)
        xor_key = buffer[block_offset + 0x0D]
        entry_count = int.from_bytes(buffer[block_offset + 0x08:block_offset + 0x0C], "little", signed=False)
        bodies.append((bytes(value ^ xor_key for value in buffer[block_offset + 0x10:block_offset + block_length]), entry_count))
    return bodies


def lenovo_lenv_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks = []
    for block_offset in find_all_bytes(buffer, b"LENV"):
        block_length = min(0x1000, len(buffer) - block_offset)
        if block_length >= 0x10:
            blocks.append((block_offset, buffer[block_offset:block_offset + block_length]))
    return blocks


LENOVO_STANDARD_DMI_MARKERS = (b"_SM_", b"_SM3_", b"SMBIOS", b"MSDM", b"SLIC")
LENOVO_FALLBACK_ANCHORS = (b"SDK0J", b"SDK0L", b" WIN")
LENOVO_FALLBACK_WINDOW = 0x400
LENOVO_FALLBACK_BLOCK_SIZE = 0x1000


def has_standard_dmi_marker(buffer: bytes) -> bool:
    return any(marker in buffer for marker in LENOVO_STANDARD_DMI_MARKERS)


def lenovo_fallback_anchor_offsets(buffer: bytes) -> list[int]:
    offsets = []
    for anchor in LENOVO_FALLBACK_ANCHORS:
        offsets.extend(find_all_bytes(buffer, anchor))
    offsets.extend(match.start() for match in WINKEY_PATTERN.finditer(buffer))
    return sorted(set(offsets))


def lenovo_fallback_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    if has_standard_dmi_marker(buffer):
        return []
    offsets = lenovo_fallback_anchor_offsets(buffer)
    blocks = []
    used_ranges: list[tuple[int, int]] = []
    for offset in offsets:
        nearby = [candidate for candidate in offsets if abs(candidate - offset) <= LENOVO_FALLBACK_WINDOW]
        if len(nearby) < 2:
            continue
        start = max(0, min(nearby) & ~(LENOVO_FALLBACK_BLOCK_SIZE - 1))
        end = min(len(buffer), start + LENOVO_FALLBACK_BLOCK_SIZE)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        used_ranges.append((start, end))
        blocks.append((start, buffer[start:end]))
    return blocks


def lenovo_dmi_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks = lenovo_lenv_blocks(buffer)
    return blocks if blocks else lenovo_fallback_blocks(buffer)


def lenovo_dmi_package_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks = lenovo_dmi_blocks(buffer)
    used_ranges = [(offset, offset + len(block)) for offset, block in blocks]
    for candidate in find_winkeys(buffer):
        start = candidate.offset & ~(0x1000 - 1)
        end = min(len(buffer), start + 0x1000)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        block = buffer[start:end]
        used_ranges.append((start, end))
        blocks.append((start, block))
    return blocks


def lenovo_dmi_export_name(source: Path) -> Path:
    return unique_output_path(source.with_name(f"{source.stem}_LENOVO_DMI.lendmi"))


def lenovo_dmi_import_output_name(target: Path) -> Path:
    suffix = target.suffix or ".bin"
    return unique_output_path(target.with_name(f"{target.stem}_LENOVO_DMI{suffix}"))


HP_DMI_ANCHORS = (
    b"Hewlett-Packard",
    b"HP Inc.",
    b"HPQ",
    b"Serial Number",
    b"Product Name",
    b"Product Number",
    b"SKU Number",
    b"Feature Byte",
    b"Build ID",
    b"System Board",
    b"System SKU",
)
HP_DMI_WINDOW = 0x800
HP_DMI_BLOCK_SIZE = 0x1000
HP_MUD_MARKER = "HP_MUD".encode("utf-16le")
HP_MUD_BLOCK_SIZE = 0x3000
HP_LEGACY_DMI_MARKER = b"$EPRF"
HP_INSYDE_DMI_MARKER = b"InsydeH2O EFI BIOS"
HP_LEGACY_DMI_BLOCK_SIZE = 0x1000
HP_RAW_DMI_BLOCK_SIZE = 0x5000
HP_CERTIFICATE_STRINGS = (
    b"UEFI Secure Boot",
    b"Microsoft",
    b"Certificate",
    b"crl",
    b"VeriSign",
)

ACER_DMI_ANCHORS = (
    b"Acer",
    b"ACER",
    b"AcerSystem",
    b"Aspire",
    b"Extensa",
    b"Nitro",
    b"Predator",
    b"Swift",
    b"TravelMate",
    b"Veriton",
)
ACER_DMI_WINDOW = 0x1200
ACER_DMI_BLOCK_SIZE = 0x2000
ACER_SERIAL_RE = r"(?:NX|NB|DT|DQ|PT|PS|UT|UD|MR)[A-Z0-9]{8,25}"
ACER_SERIAL_PATTERN = re.compile(ACER_SERIAL_RE)
ACER_SERIAL_BYTES_PATTERN = re.compile(ACER_SERIAL_RE.encode("ascii"))
ACER_MODEL_FAMILIES = ("aspire", "extensa", "nitro", "predator", "swift", "travelmate", "veriton")
ACER_CERTIFICATE_TERMS = (
    "root ca",
    "platform key",
    "key exchange key",
    "database",
    "database forbidden",
    "certificate",
)


def hp_dmi_anchor_offsets(buffer: bytes) -> list[int]:
    offsets = []
    lower = buffer.lower()
    for anchor in HP_DMI_ANCHORS:
        offsets.extend(find_all_bytes(buffer, anchor))
        lower_anchor = anchor.lower()
        if lower_anchor != anchor:
            offsets.extend(find_all_bytes(lower, lower_anchor))
    offsets.extend(match.start() for match in WINKEY_PATTERN.finditer(buffer))
    return sorted(set(offsets))


def hp_dmi_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    legacy_blocks = hp_legacy_dmi_blocks(buffer)
    if legacy_blocks:
        return legacy_blocks
    mud_blocks = hp_mud_blocks(buffer)
    if mud_blocks:
        return mud_blocks
    offsets = hp_dmi_anchor_offsets(buffer)
    blocks = []
    used_ranges: list[tuple[int, int]] = []
    for offset in offsets:
        nearby = [candidate for candidate in offsets if abs(candidate - offset) <= HP_DMI_WINDOW]
        if len(nearby) < 2:
            continue
        start = max(0, min(nearby) & ~(HP_DMI_BLOCK_SIZE - 1))
        end = min(len(buffer), start + HP_DMI_BLOCK_SIZE)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        block = buffer[start:end]
        if any(marker in block for marker in HP_CERTIFICATE_STRINGS):
            continue
        used_ranges.append((start, end))
        blocks.append((start, block))
    return blocks


def hp_dmi_package_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks = hp_dmi_blocks(buffer)
    used_ranges = [(offset, offset + len(block)) for offset, block in blocks]
    for candidate in find_winkeys(buffer):
        start = candidate.offset & ~(0x1000 - 1)
        end = min(len(buffer), start + 0x1000)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        block = buffer[start:end]
        used_ranges.append((start, end))
        blocks.append((start, block))
    return blocks


def hp_legacy_dmi_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks = []
    used_ranges: list[tuple[int, int]] = []
    offsets = list(find_all_bytes(buffer, HP_LEGACY_DMI_MARKER))
    offsets.extend(find_all_bytes(buffer, HP_INSYDE_DMI_MARKER))
    for match in re.finditer(rb"(?<![A-Z0-9])[A-Z0-9]{10}\x00", buffer):
        block_start = match.start() & ~(HP_DMI_BLOCK_SIZE - 1)
        block = buffer[block_start:block_start + HP_DMI_BLOCK_SIZE]
        if b"HP " in block and re.search(rb"[A-Z0-9]{5,8}#[A-Z0-9]{3}", block):
            offsets.append(match.start())
    for offset in sorted(set(offsets)):
        start = max(0, offset & ~(HP_DMI_BLOCK_SIZE - 1))
        block_size = HP_RAW_DMI_BLOCK_SIZE if HP_INSYDE_DMI_MARKER not in buffer[start:start + HP_LEGACY_DMI_BLOCK_SIZE] else HP_LEGACY_DMI_BLOCK_SIZE
        end = min(len(buffer), start + block_size)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        block = buffer[start:end]
        if b"HP " in block and (
            b"#" in block
            or WINKEY_PATTERN.search(block)
            or HP_INSYDE_DMI_MARKER in block
        ):
            used_ranges.append((start, end))
            blocks.append((start, block))
    return blocks


def hp_mud_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks = []
    used_ranges: list[tuple[int, int]] = []
    for offset in find_all_bytes(buffer, HP_MUD_MARKER):
        start = max(0, offset & ~(HP_DMI_BLOCK_SIZE - 1))
        end = min(len(buffer), start + HP_MUD_BLOCK_SIZE)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        block = buffer[start:end]
        if "HP_MUD".encode("utf-16le") in block and (
            "BuildId".encode("utf-16le") in block or "FactoryConfig".encode("utf-16le") in block
        ):
            used_ranges.append((start, end))
            blocks.append((start, block))
    return blocks


def hp_dmi_export_name(source: Path) -> Path:
    return unique_output_path(source.with_name(f"{source.stem}_HP_DMI.hpdmi"))


def hp_dmi_import_output_name(target: Path) -> Path:
    suffix = target.suffix or ".bin"
    return unique_output_path(target.with_name(f"{target.stem}_HP_DMI{suffix}"))


def acer_dmi_anchor_offsets(buffer: bytes) -> list[int]:
    offsets: list[int] = []
    lower = buffer.lower()
    for anchor in ACER_DMI_ANCHORS:
        offsets.extend(find_all_bytes(buffer, anchor))
        lower_anchor = anchor.lower()
        if lower_anchor != anchor:
            offsets.extend(find_all_bytes(lower, lower_anchor))
    offsets.extend(match.start() for match in WINKEY_PATTERN.finditer(buffer))
    offsets.extend(match.start() for match in ACER_SERIAL_BYTES_PATTERN.finditer(buffer))
    return sorted(set(offsets))


def acer_dmi_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    offsets = acer_dmi_anchor_offsets(buffer)
    blocks: list[tuple[int, bytes]] = []
    used_ranges: list[tuple[int, int]] = []
    for offset in offsets:
        nearby = [candidate for candidate in offsets if abs(candidate - offset) <= ACER_DMI_WINDOW]
        if len(nearby) < 2:
            continue
        start = max(0, min(nearby) & ~(ACER_DMI_BLOCK_SIZE - 1))
        end = min(len(buffer), start + ACER_DMI_BLOCK_SIZE)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        block = buffer[start:end]
        if not acer_dmi_block_has_values(block):
            continue
        used_ranges.append((start, end))
        blocks.append((start, block))
    if blocks:
        return blocks
    for offset in find_all_bytes(buffer, b"Acer"):
        start = max(0, offset & ~(ACER_DMI_BLOCK_SIZE - 1))
        end = min(len(buffer), start + ACER_DMI_BLOCK_SIZE)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        block = buffer[start:end]
        if not acer_dmi_block_has_values(block):
            continue
        used_ranges.append((start, end))
        blocks.append((start, block))
    return blocks


def acer_dmi_package_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks = acer_dmi_blocks(buffer)
    used_ranges = [(offset, offset + len(block)) for offset, block in blocks]
    for candidate in find_winkeys(buffer):
        start = candidate.offset & ~(0x1000 - 1)
        end = min(len(buffer), start + 0x1000)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        block = buffer[start:end]
        used_ranges.append((start, end))
        blocks.append((start, block))
    return blocks


def acer_dmi_block_has_values(block: bytes) -> bool:
    values = [clean_acer_dmi_value(match.decode("ascii", errors="ignore")) for match in re.findall(rb"[ -~]{3,}", block)]
    return any(is_acer_dmi_value(value) and acer_dmi_label(value) != "Vendor" for value in values)


def clean_acer_dmi_value(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip().strip("\x00"))
    if not value:
        return ""
    lower = value.lower()
    if "uefideviceidentifierpacket" in lower:
        return ""
    if lower.startswith("acer") and any(term in lower for term in ACER_CERTIFICATE_TERMS):
        return ""
    if lower in {"acernbsetup", "acerproductinfo", "acer data"}:
        return ""
    if re.fullmatch(r"acer\d*(?:\s+0)?", lower):
        return "Acer"
    for family in ACER_MODEL_FAMILIES:
        match = re.search(rf"{family}\b", value, re.IGNORECASE)
        if match:
            return value[match.start():].strip()
    return value


def acer_dmi_label(value: str) -> str:
    lower = value.lower()
    if WINKEY_PATTERN.fullmatch(value.encode("ascii", errors="ignore")):
        return "Windows Key"
    if re.fullmatch(r"\d{10,13}", value):
        return "SNID"
    if ACER_SERIAL_PATTERN.fullmatch(value):
        if value.startswith("NX"):
            return "System Serial Number"
        if value.startswith("NB"):
            return "Board Serial Number"
        return "Serial Number"
    if any(name in lower for name in ACER_MODEL_FAMILIES):
        return "Model"
    if lower.startswith("acer"):
        return "Vendor"
    return "Acer DMI"


def is_acer_dmi_value(value: str) -> bool:
    lower = value.lower()
    if not value or len(value) > 96:
        return False
    if lower.startswith("acer") and any(term in lower for term in ACER_CERTIFICATE_TERMS):
        return False
    if lower in {"acernbsetup", "acerproductinfo", "acer data"}:
        return False
    return (
        WINKEY_PATTERN.fullmatch(value.encode("ascii", errors="ignore")) is not None
        or re.fullmatch(r"\d{10,13}", value) is not None
        or ACER_SERIAL_PATTERN.fullmatch(value) is not None
        or lower.startswith("acer")
        or any(name in lower for name in ACER_MODEL_FAMILIES)
    )


def find_acer_dmi(buffer: bytes) -> list[LenovoDmiItem]:
    items: list[LenovoDmiItem] = []
    for _start, _end, block_items in find_acer_dmi_groups(buffer):
        items.extend(block_items)
    return items


def find_acer_dmi_groups(buffer: bytes) -> list[tuple[int, int, list[LenovoDmiItem]]]:
    groups: list[tuple[int, int, list[LenovoDmiItem]]] = []
    seen: set[tuple[str, str]] = set()
    for block_offset, block in acer_dmi_blocks(buffer):
        block_items: list[LenovoDmiItem] = []
        for match in re.finditer(rb"[ -~]{3,}", block):
            raw_value = match.group(0).decode("ascii", errors="ignore")
            value = clean_acer_dmi_value(raw_value)
            if not value or not is_acer_dmi_value(value):
                continue
            label = acer_dmi_label(value)
            key = (label, value)
            if key in seen:
                continue
            seen.add(key)
            raw_start = block_offset + match.start()
            value_delta = raw_value.find(value)
            start = raw_start + value_delta if value_delta >= 0 else raw_start
            end = start + len(value.encode("ascii", errors="ignore"))
            item = LenovoDmiItem(label, value, start, end)
            block_items.append(item)
        if block_items:
            groups.append((block_offset, block_offset + len(block), block_items))
    for candidate in find_winkeys(buffer):
        classification = re.sub(r"^likely\s+", "", candidate.classification, flags=re.IGNORECASE)
        value = f"{candidate.key} | {classification}" if classification else candidate.key
        key = ("Windows Product Key", value)
        if key in seen:
            continue
        seen.add(key)
        start = candidate.offset & ~(0x1000 - 1)
        end = min(len(buffer), start + 0x1000)
        item = LenovoDmiItem("Windows Product Key", value, candidate.offset, candidate.offset + len(candidate.key))
        groups.append((start, end, [item]))
    return groups


def acer_dmi_export_name(source: Path) -> Path:
    return unique_output_path(source.with_name(f"{source.stem}_ACER_DMI.acerdmi"))


def acer_dmi_import_output_name(target: Path) -> Path:
    suffix = target.suffix or ".bin"
    return unique_output_path(target.with_name(f"{target.stem}_ACER_DMI{suffix}"))


def export_acer_dmi(source: Path) -> tuple[Path, int]:
    blocks = acer_dmi_package_blocks(source.read_bytes())
    if not blocks:
        raise RuntimeError("Acer DMI block was not found.")
    payload = dmi_package_payload("Acer", blocks)
    output = acer_dmi_export_name(source)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output, len(blocks)


def dell_dmi_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks: list[tuple[int, bytes]] = []
    used: set[int] = set()
    if len(buffer) >= DELL_IDENTITY_BLOCK_SIZE:
        identity = buffer[:DELL_IDENTITY_BLOCK_SIZE]
        ppid_match = re.match(rb"CN[A-Z0-9]{20,}", identity[0x10:0x30])
        ppid = ppid_match.group(0) if ppid_match else b""
        tag = identity[0x30:0x37]
        if re.fullmatch(rb"CN[A-Z0-9]{20,}", ppid) and DELL_SERVICE_TAG_PATTERN.fullmatch(tag):
            blocks.append((0, identity))
            used.add(0)
    anchors = [
        *find_all_bytes(buffer, DELL_DMI_MARKER),
        *find_all_bytes(buffer, DELL_SERVICE_TAG_NAME),
        *find_all_bytes(buffer, DELL_EPPID_NAME),
    ]
    for anchor in anchors:
        start = max(0, anchor & ~(DELL_DMI_BLOCK_SIZE - 1))
        end = min(len(buffer), start + DELL_DMI_BLOCK_SIZE)
        if start in used:
            continue
        block = buffer[start:end]
        if DELL_DMI_MARKER not in block:
            continue
        if DELL_SERVICE_TAG_NAME not in block and DELL_EPPID_NAME not in block:
            continue
        used.add(start)
        blocks.append((start, block))
    for match in DELL_MODEL_BYTES_PATTERN.finditer(buffer):
        anchor = match.start()
        if anchor >= 0x100000:
            continue
        start = max(0, anchor & ~(DELL_DMI_BLOCK_SIZE - 1))
        end = min(len(buffer), start + DELL_DMI_BLOCK_SIZE)
        if start in used:
            continue
        block = buffer[start:end]
        if not DELL_MODEL_BYTES_PATTERN.search(block):
            continue
        used.add(start)
        blocks.append((start, block))
    return blocks


def dell_dmi_package_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks = dell_dmi_blocks(buffer)
    used_ranges = [(offset, offset + len(block)) for offset, block in blocks]
    for candidate in find_winkeys(buffer):
        start = candidate.offset & ~(0x1000 - 1)
        end = min(len(buffer), start + 0x1000)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        block = buffer[start:end]
        used_ranges.append((start, end))
        blocks.append((start, block))
    return blocks


def dell_ascii_after_name(block: bytes, name: bytes, limit: int = 128) -> str:
    offset = block.find(name)
    if offset < 0:
        return ""
    start = offset + len(name)
    for match in re.finditer(rb"[ -~]{3,}", block[start:start + limit]):
        value = match.group().decode("ascii", errors="ignore").strip()
        if value:
            return value
    return ""


def find_dell_dmi(buffer: bytes) -> list[LenovoDmiItem]:
    items: list[LenovoDmiItem] = []
    for _start, _end, block_items in find_dell_dmi_groups(buffer):
        items.extend(block_items)
    return items


def find_dell_dmi_groups(buffer: bytes) -> list[tuple[int, int, list[LenovoDmiItem]]]:
    groups: list[tuple[int, int, list[LenovoDmiItem]]] = []
    seen: set[tuple[str, str]] = set()

    def add_item(target: list[LenovoDmiItem], label: str, value: str) -> None:
        value = value.strip().strip("$").strip()
        if not value or (label, value) in seen:
            return
        seen.add((label, value))
        target.append(LenovoDmiItem(label, value))

    if len(buffer) >= DELL_IDENTITY_BLOCK_SIZE:
        identity = buffer[:DELL_IDENTITY_BLOCK_SIZE]
        ppid_match = re.match(rb"CN[A-Z0-9]{20,}", identity[0x10:0x30])
        identity_items: list[LenovoDmiItem] = []
        values = [
            ("Service Tag", identity[0x30:0x37].decode("ascii", errors="ignore")),
            ("PPID", ppid_match.group(0).decode("ascii", errors="ignore") if ppid_match else ""),
        ]
        for label, value in values:
            if label == "Service Tag" and not DELL_SERVICE_TAG_PATTERN.fullmatch(value.encode("ascii", errors="ignore")):
                continue
            if label == "PPID" and not value.startswith("CN"):
                continue
            add_item(identity_items, label, value)
        if identity_items:
            groups.append((0, DELL_IDENTITY_BLOCK_SIZE, identity_items))
    blocks = dell_dmi_blocks(buffer)
    for offset, block in blocks:
        if len(block) == DELL_IDENTITY_BLOCK_SIZE:
            continue
        block_items: list[LenovoDmiItem] = []
        values = [
            ("Service Tag", dell_ascii_after_name(block, DELL_SERVICE_TAG_NAME)),
            ("PPID", dell_ascii_after_name(block, DELL_EPPID_NAME, 256)),
        ]
        for label, value in values:
            if label == "Service Tag" and not DELL_SERVICE_TAG_PATTERN.fullmatch(value.encode("ascii", errors="ignore")):
                continue
            if not value or (label, value) in seen:
                continue
            add_item(block_items, label, value)
        for match in re.findall(rb"[ -~]{4,}", block):
            value = match.decode("ascii", errors="ignore").strip()
            if DELL_MODEL_PATTERN.fullmatch(value):
                add_item(block_items, "Model", value)
            elif WINKEY_PATTERN.fullmatch(match):
                add_item(block_items, "Windows Product Key", value)
        if block_items and not any(item.label == "Service Tag" for item in block_items):
            add_item(block_items, "Service Tag", "Encoded, can not parse")
        elif not block_items and (DELL_DMI_MARKER in block or DELL_SERVICE_TAG_NAME in block or DELL_EPPID_NAME in block):
            add_item(block_items, "Service Tag", "Encoded, can not parse")
        if block_items:
            order = {"Service Tag": 0, "Model": 1, "Windows Product Key": 2, "PPID": 3}
            block_items.sort(key=lambda item: order.get(item.label, 99))
            groups.append((offset, offset + len(block), block_items))
    used_ranges = [(start, end) for start, end, _items in groups]
    for candidate in find_winkeys(buffer):
        classification = re.sub(r"^likely\s+", "", candidate.classification, flags=re.IGNORECASE)
        value = f"{candidate.key} | {classification}" if classification else candidate.key
        key = ("Windows Product Key", value)
        if key in seen:
            continue
        seen.add(key)
        start = candidate.offset & ~(0x1000 - 1)
        end = min(len(buffer), start + 0x1000)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        groups.append((start, end, [LenovoDmiItem("Windows Product Key", value, candidate.offset, candidate.offset + len(candidate.key))]))
    return groups


def dell_dmi_export_name(source: Path) -> Path:
    return unique_output_path(source.with_name(f"{source.stem}_DELL_DMI.delldmi"))


def dell_dmi_import_output_name(target: Path) -> Path:
    suffix = target.suffix or ".bin"
    return unique_output_path(target.with_name(f"{target.stem}_DELL_DMI{suffix}"))


def export_dell_dmi(source: Path) -> tuple[Path, int]:
    blocks = dell_dmi_package_blocks(source.read_bytes())
    if not blocks:
        raise RuntimeError("Dell DMI block was not found.")
    payload = dmi_package_payload("Dell", blocks)
    output = dell_dmi_export_name(source)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output, len(blocks)


def dmi_block_meta(offset: int, block: bytes) -> dict:
    return {
        "offset": offset,
        "size": len(block),
        "sha256": hashlib.sha256(block).hexdigest(),
        "head": base64.b64encode(block[:64]).decode("ascii"),
    }


def dmi_package_payload(kind: str, blocks: list[tuple[int, bytes]]) -> dict:
    return {
        "format": f"AutoClearME {kind} DMI",
        "version": 2,
        "kind": kind,
        "blocks": [base64.b64encode(block).decode("ascii") for _offset, block in blocks],
        "block_meta": [dmi_block_meta(offset, block) for offset, block in blocks],
    }


def export_lenovo_dmi(source: Path) -> tuple[Path, int]:
    blocks = lenovo_dmi_package_blocks(source.read_bytes())
    if not blocks:
        raise RuntimeError("Lenovo DMI block was not found.")
    payload = dmi_package_payload("Lenovo", blocks)
    output = lenovo_dmi_export_name(source)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output, len(blocks)


def export_hp_dmi(source: Path) -> tuple[Path, int]:
    blocks = hp_dmi_package_blocks(source.read_bytes())
    if not blocks:
        raise RuntimeError("HP DMI block was not found.")
    payload = dmi_package_payload("HP", blocks)
    output = hp_dmi_export_name(source)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output, len(blocks)


def load_dmi_package(path: Path) -> tuple[str, list[bytes], list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    package_format = str(payload.get("format") or "")
    valid_formats = {
        "autoclearme lenovo dmi",
        "autoclearme hp dmi",
        "autoclearme acer dmi",
        "autoclearme asus dmi",
        "autoclearme dell dmi",
    }
    if package_format.lower() not in valid_formats:
        raise RuntimeError("Invalid DMI package.")
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise RuntimeError("DMI package does not contain any block.")
    kind = str(payload.get("kind") or package_format.replace("AutoClearME ", "").replace(" DMI", ""))
    block_meta = payload.get("block_meta")
    meta = block_meta if isinstance(block_meta, list) else []
    return kind, [base64.b64decode(str(block)) for block in blocks], meta


def dmi_block_markers(block: bytes) -> tuple[bytes, ...]:
    markers = []
    for marker in (
        HP_LEGACY_DMI_MARKER,
        HP_INSYDE_DMI_MARKER,
        HP_MUD_MARKER,
        b"HP ",
        b"LENV",
        b"Acer",
        b"$DMI",
        b"$BVDT",
        WINKEY_OEM_MARKER,
        b"MFG0\x00",
        DELL_DMI_MARKER,
    ):
        if marker in block:
            markers.append(marker)
    return tuple(markers)


def match_dmi_import_blocks(
    kind: str,
    blocks: list[bytes],
    block_meta: list[dict],
    target_blocks: list[tuple[int, bytes]],
) -> list[tuple[bytes, int, bytes]]:
    if len(target_blocks) < len(blocks):
        if kind.lower() != "hp" or not block_meta:
            raise RuntimeError(f"Target BIOS has {len(target_blocks)} {kind} DMI block(s), package has {len(blocks)}.")
    matches: list[tuple[bytes, int, bytes]] = []
    used_targets: set[int] = set()
    require_exact_offset = kind.lower() == "hp" and bool(block_meta) and len(target_blocks) < len(blocks)
    for index, block in enumerate(blocks):
        meta = block_meta[index] if index < len(block_meta) and isinstance(block_meta[index], dict) else {}
        wanted_offset = meta.get("offset")
        wanted_size = int(meta.get("size") or len(block))
        markers = dmi_block_markers(block)
        selected: tuple[int, bytes] | None = None
        if isinstance(wanted_offset, int):
            for target_index, (offset, target_block) in enumerate(target_blocks):
                if target_index in used_targets:
                    continue
                if offset == wanted_offset and len(target_block) == wanted_size:
                    selected = (offset, target_block)
                    used_targets.add(target_index)
                    break
        if selected is None:
            if require_exact_offset:
                continue
            for target_index, (offset, target_block) in enumerate(target_blocks):
                if target_index in used_targets or len(target_block) != len(block):
                    continue
                if markers and not all(marker in target_block for marker in markers):
                    continue
                selected = (offset, target_block)
                used_targets.add(target_index)
                break
        if selected is None:
            if kind.lower() == "hp" and block_meta:
                continue
            raise RuntimeError(f"Target BIOS does not contain a matching {kind} DMI block for package block {index + 1}.")
        matches.append((block, selected[0], selected[1]))
    if not matches:
        raise RuntimeError(f"Target BIOS does not contain any matching {kind} DMI block.")
    return matches


def import_dmi_package(target: Path, dmi_package: Path) -> tuple[Path, int, str]:
    kind, blocks, block_meta = load_dmi_package(dmi_package)
    data = bytearray(target.read_bytes())
    kind_lower = kind.lower()
    if kind_lower == "hp":
        target_blocks = hp_dmi_package_blocks(data)
        output_name = hp_dmi_import_output_name(target)
    elif kind_lower == "asus":
        target_blocks = asus_dmi_package_blocks(data)
        output_name = asus_dmi_import_output_name(target)
    elif kind_lower == "acer":
        target_blocks = acer_dmi_package_blocks(data)
        output_name = acer_dmi_import_output_name(target)
    elif kind_lower == "dell":
        target_blocks = dell_dmi_package_blocks(data)
        output_name = dell_dmi_import_output_name(target)
    else:
        target_blocks = lenovo_dmi_package_blocks(data)
        output_name = lenovo_dmi_import_output_name(target)
    block_pairs = match_dmi_import_blocks(kind, blocks, block_meta, target_blocks)
    for block, offset, target_block in block_pairs:
        if len(block) != len(target_block):
            raise RuntimeError(f"{kind} DMI block size does not match target BIOS.")
        data[offset:offset + len(block)] = block
    output_name.write_bytes(data)
    return output_name, len(block_pairs), kind


def import_lenovo_dmi(target: Path, dmi_package: Path) -> tuple[Path, int]:
    output, count, _kind = import_dmi_package(target, dmi_package)
    return output, count


LENOVO_DMI_LABELS = {
    0x0000: "Product Name",
    0x0001: "Board ID",
    0x0002: "MTM",
    0x0003: "System ID",
    0x0004: "Serial Number",
    0x000F: "Platform ID",
    0x0010: "OS",
    0x0100: "Windows Product Key",
    0x0B00: "UUID/ID",
}


def lenovo_lenv_payloads(body: bytes, entry_count: int) -> list[tuple[int, bytes]]:
    payloads = []
    entry_offset = 0
    if not 0 < entry_count <= 256:
        entry_count = 256
    for _entry_index in range(entry_count):
        if entry_offset + 0x18 > len(body):
            break
        data_size = int.from_bytes(body[entry_offset + 0x10:entry_offset + 0x14], "little", signed=False)
        if data_size <= 0 or data_size > len(body) - entry_offset - 0x18:
            break
        entry_id = int.from_bytes(body[entry_offset + 0x0E:entry_offset + 0x10], "big", signed=False)
        payloads.append((entry_id, body[entry_offset + 0x18:entry_offset + 0x18 + data_size]))
        entry_offset += 0x18 + data_size
    return payloads


def clean_lenovo_dmi_value(value: str) -> str:
    value = value.strip().strip("\x00")
    return value[:-2] if value.endswith("UW") else value


def lenovo_dmi_label(value: str) -> str:
    if WINKEY_PATTERN.fullmatch(value.encode("ascii", errors="ignore")):
        return "Windows Product Key"
    if value in {"WIN", "NO DPK"}:
        return "OS"
    if re.fullmatch(r"8[A-Z0-9]{7,11}", value):
        return "MTM"
    if re.fullmatch(r"[A-Z0-9]{7,10}", value) and not value.startswith("SDK"):
        return "Serial Number"
    if value.startswith("SDK"):
        return "Platform ID"
    if re.search(r"(Think|Idea|Yoga|Legion|Lenovo|XiaoXin)", value, re.IGNORECASE):
        return "Product Name"
    if re.fullmatch(r"[0-9]{13,16}", value):
        return "UUID/ID"
    if value.startswith("LNV"):
        return "Board ID"
    return "Lenovo DMI"


def lenovo_dmi_entry_label(entry_id: int, value: str) -> str:
    return LENOVO_DMI_LABELS.get(entry_id) or lenovo_dmi_label(value)


def find_lenovo_dmi(buffer: bytes) -> list[LenovoDmiItem]:
    items: list[LenovoDmiItem] = []
    for _start, _end, block_items in find_lenovo_dmi_groups(buffer):
        items.extend(block_items)
    return items


def find_lenovo_dmi_groups(buffer: bytes) -> list[tuple[int, int, list[LenovoDmiItem]]]:
    groups: list[tuple[int, int, list[LenovoDmiItem]]] = []
    seen: set[str] = set()
    for block_offset, block in lenovo_lenv_blocks(buffer):
        xor_key = block[0x0D] if len(block) > 0x0D else 0
        entry_count = int.from_bytes(block[0x08:0x0C], "little", signed=False) if len(block) >= 0x0C else 0
        body = bytes(value ^ xor_key for value in block[0x10:])
        block_items: list[LenovoDmiItem] = []
        for entry_id, payload in lenovo_lenv_payloads(body, entry_count):
            for match in re.findall(rb"[ -~]{3,}", payload):
                value = clean_lenovo_dmi_value(match.decode("ascii", errors="ignore"))
                if not is_lenovo_dmi_value(value) or value in seen:
                    continue
                seen.add(value)
                block_items.append(LenovoDmiItem(lenovo_dmi_entry_label(entry_id, value), value))
        if block_items:
            groups.append((block_offset, block_offset + len(block), block_items))
    if groups:
        append_lenovo_winkey_groups(buffer, groups, seen)
        return groups
    for block_offset, block in lenovo_fallback_blocks(buffer):
        block_items = []
        for match in re.findall(rb"[ -~]{3,}", block):
            value = clean_lenovo_dmi_value(match.decode("ascii", errors="ignore"))
            if not value or value in seen:
                continue
            if not (
                value.startswith(("SDK0J", "SDK0L"))
                or value == "WIN"
                or value.endswith(" WIN")
                or WINKEY_PATTERN.fullmatch(value.encode("ascii", errors="ignore"))
            ):
                continue
            seen.add(value)
            block_items.append(LenovoDmiItem(lenovo_dmi_label(value), value.strip()))
        if block_items:
            groups.append((block_offset, block_offset + len(block), block_items))
    append_lenovo_winkey_groups(buffer, groups, seen)
    return groups


def append_lenovo_winkey_groups(buffer: bytes, groups: list[tuple[int, int, list[LenovoDmiItem]]], seen: set[str]) -> None:
    used_ranges = [(start, end) for start, end, _items in groups]
    for candidate in find_winkeys(buffer):
        classification = re.sub(r"^likely\s+", "", candidate.classification, flags=re.IGNORECASE)
        value = f"{candidate.key} | {classification}" if classification else candidate.key
        if value in seen:
            continue
        seen.add(value)
        start = candidate.offset & ~(0x1000 - 1)
        end = min(len(buffer), start + 0x1000)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        groups.append((start, end, [LenovoDmiItem("Windows Product Key", value, candidate.offset, candidate.offset + len(candidate.key))]))


def is_lenovo_dmi_value(value: str) -> bool:
    if not value or len(value) > 96:
        return False
    if WINKEY_PATTERN.fullmatch(value.encode("ascii", errors="ignore")):
        return True
    if re.fullmatch(r"[A-Z0-9]{7,10}", value) and not value.startswith(("82", "SDK")):
        return True
    if value.startswith("82") and re.fullmatch(r"[A-Z0-9]{8,12}", value):
        return True
    if value.startswith("SDK"):
        return True
    if re.search(r"(Think|Idea|Yoga|Legion|Lenovo|XiaoXin)", value, re.IGNORECASE):
        return True
    if re.fullmatch(r"[0-9]{13,16}", value):
        return True
    if value.startswith("LNV"):
        return True
    if value in {"WIN", "NO DPK"}:
        return True
    return False


def hp_dmi_label(value: str) -> str:
    lower = value.lower()
    if WINKEY_PATTERN.fullmatch(value.encode("ascii", errors="ignore")):
        return "Windows Product Key"
    if "serial" in lower:
        return "Serial Number"
    if "product" in lower:
        return "Product"
    if "sku" in lower:
        return "SKU"
    if "feature" in lower:
        return "Feature Byte"
    if "build" in lower:
        return "Build ID"
    if "system board" in lower:
        return "System Board"
    if value.startswith(("HP", "Hewlett")):
        return "Vendor"
    return "HP DMI"


def utf16le_strings_with_offsets(buffer: bytes, base_offset: int = 0) -> list[tuple[int, str]]:
    values = []
    chars: list[str] = []
    start: int | None = None
    for index in range(0, len(buffer) - 1, 2):
        code = buffer[index] | (buffer[index + 1] << 8)
        if 32 <= code < 127:
            if start is None:
                start = base_offset + index
            chars.append(chr(code))
        else:
            if start is not None and len(chars) >= 3:
                values.append((start, "".join(chars)))
            chars = []
            start = None
    if start is not None and len(chars) >= 3:
        values.append((start, "".join(chars)))
    return values


BIOS_VERSION_PATTERNS = {
    "Dell": re.compile(r"(?:A\d{2}|\d{1,2}\.\d{1,2}\.\d{1,3})", re.IGNORECASE),
    "Lenovo": re.compile(r"[A-Z0-9]{3,5}ET\d{2}W(?:\s*\([^)]+\))?", re.IGNORECASE),
    "HP": re.compile(r"(?:[A-Z0-9]{1,4}\s+Ver\.\s+[A-Z0-9.]+|F\.\d{2}(?:\.\d{2})?)", re.IGNORECASE),
    "Acer": re.compile(r"V\d+\.\d+(?:\.\d+)?", re.IGNORECASE),
    "ASUS": re.compile(r"(?:[A-Z][A-Z0-9-]{2,20}(?:AS)?\.)?\d{3,4}", re.IGNORECASE),
}


def firmware_text_strings(buffer: bytes) -> list[tuple[int, str]]:
    strings = [
        (match.start(), match.group().decode("ascii", errors="ignore"))
        for match in re.finditer(rb"[ -~]{3,128}", buffer)
    ]
    strings.extend(utf16le_strings_with_offsets(buffer))
    strings.extend(utf16le_strings_with_offsets(buffer[1:], 1))
    return sorted(set(strings), key=lambda item: item[0])


def bios_vendor_from_strings(strings: list[tuple[int, str]]) -> str:
    text = "\n".join(value.lower() for _offset, value in strings)
    markers = (
        ("Dell", ("dell inc", "dell computer", "optiplex", "latitude", "vostro", "inspiron")),
        ("Lenovo", ("lenovo", "thinkpad", "thinkcentre")),
        ("HP", ("hewlett-packard", "hewlett packard", "elitebook", "probook", "zbook")),
        ("Acer", ("acer incorporated", "acer inc", "aspire", "travelmate")),
        ("ASUS", ("asustek", "asus computer")),
    )
    for vendor, values in markers:
        if any(marker in text for marker in values):
            return vendor
    return ""


def clean_bios_version_candidate(value: str) -> str:
    value = re.sub(r"(?i)^.*?bios\s*(?:version|revision|id)\s*[:=\-]?\s*", "", value).strip()
    return value.strip(" \t\r\n\x00:;,-_[]{}")


def detect_asus_bios_header(buffer: bytes) -> tuple[str, str]:
    date_pattern = re.compile(rb"\d{2}/\d{2}/\d{4}")
    model_pattern = re.compile(rb"[A-Z]{1,4}\d{3,4}[A-Z]{0,3}")
    for date_match in date_pattern.finditer(buffer):
        start = max(0, date_match.start() - 128)
        prefix = buffer[start:date_match.start()]
        if b"ASUS" not in prefix and b"$MODIFYSIG$" not in prefix:
            continue
        models = [match.group().decode("ascii") for match in model_pattern.finditer(prefix)]
        parts = [match.group().decode("ascii") for match in re.finditer(rb"(?<!\d)\d{1,2}(?=\x00)", prefix)]
        for index in range(len(parts) - 1):
            version = parts[index] + parts[index + 1]
            if len(version) == 3 and version.isdigit():
                return (models[-1] if models else ""), version
    return "", ""


def detect_bios_version(buffer: bytes) -> tuple[str, str]:
    _asus_model, asus_version = detect_asus_bios_header(buffer)
    if asus_version:
        return "ASUS", asus_version
    strings = firmware_text_strings(buffer)
    vendor = bios_vendor_from_strings(strings)
    if not vendor:
        return "", ""
    pattern = BIOS_VERSION_PATTERNS[vendor]

    for index, (offset, value) in enumerate(strings):
        if not re.search(r"(?i)\bbios\s*(?:version|revision|id)\b", value):
            continue
        candidates = [clean_bios_version_candidate(value)]
        candidates.extend(
            candidate
            for next_offset, candidate in strings[index + 1:index + 7]
            if next_offset - offset <= 512
        )
        for candidate in candidates:
            match = pattern.fullmatch(candidate.strip())
            if match:
                return vendor, match.group(0)

    if vendor in {"Lenovo", "HP"}:
        for _offset, value in strings:
            match = pattern.fullmatch(value.strip())
            if match:
                return vendor, match.group(0)
    return vendor, ""


ASUS_MFG_MARKER = b"MFG0\x00"
ASUS_DMI_RECORD_SIZE = 0x104
ASUS_MODEL_PATTERN = re.compile(r"^(?:[A-Z]{1,4}\d{3,4}[A-Z]{0,3})(?:[.-][A-Z0-9]+)?$", re.IGNORECASE)
ASUS_BOARD_PART_PATTERN = re.compile(r"^90N[A-Z0-9]{4,8}-[A-Z0-9]{4,8}$", re.IGNORECASE)
ASUS_SERIAL_PATTERN = re.compile(r"^[A-Z0-9]{10,20}$", re.IGNORECASE)
ASUS_BOARD_ID_PATTERN = re.compile(r"^[A-Z0-9]{8,32}$", re.IGNORECASE)
ASUS_MFG_SLOTS = (
    ("Board Serial Number", 0x05, 0x1E),
    ("Board Part Number", 0x1E, 0x32),
    ("Board ID", 0x32, 0x55),
    ("System Identifier", 0x55, 0x69),
    ("Configuration ID", 0x69, 0x73),
    ("Configuration Code 1", 0x73, 0x77),
    ("Configuration Code 2", 0x77, 0x85),
    ("Model Identifier", 0x85, 0x99),
    ("Manufacture Date", 0x99, ASUS_DMI_RECORD_SIZE),
)


def asus_mfg_values(buffer: bytes) -> list[list[str]]:
    records = []
    for offset in find_all_bytes(buffer, ASUS_MFG_MARKER):
        end = buffer.find(b"NVAR", offset + len(ASUS_MFG_MARKER), offset + 0x300)
        payload = buffer[offset + len(ASUS_MFG_MARKER):end if end >= 0 else offset + 0x180]
        values = []
        for chunk in re.split(rb"\xFF+", payload):
            for match in re.finditer(rb"[ -~]{2,64}", chunk):
                value = match.group().decode("ascii", errors="ignore").strip(" \x00")
                if value and value not in {"MFG0", "GPNV", "CNFG"}:
                    values.append(value)
        if values and values not in records:
            records.append(values)
    return records


def asus_mfg_fields(block: bytes) -> dict[str, str]:
    fields = {}
    for label, start, end in ASUS_MFG_SLOTS:
        raw = block[start:end].split(b"\x00", 1)[0].split(b"\xFF", 1)[0]
        value = raw.decode("ascii", errors="ignore").strip()
        if label == "Manufacture Date":
            match = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?", value)
            value = match.group(0) if match else value
        if value:
            fields[label] = value
    config = fields.get("Configuration ID", "")
    if "Model Identifier" not in fields and "." in config:
        model = config.split(".", 1)[0]
        if ASUS_MODEL_PATTERN.fullmatch(model):
            fields["Model Identifier"] = model
    return fields


def find_asus_dmi(buffer: bytes) -> list[LenovoDmiItem]:
    items: list[LenovoDmiItem] = []
    for _start, _end, block_items in find_asus_dmi_groups(buffer):
        items.extend(block_items)
    return items


def find_asus_dmi_groups(buffer: bytes) -> list[tuple[int, int, list[LenovoDmiItem]]]:
    groups: list[tuple[int, int, list[LenovoDmiItem]]] = []
    seen: set[tuple[str, str]] = set()
    blocks = asus_dmi_blocks(buffer)
    if blocks:
        best_offset, best_block = max(
            blocks,
            key=lambda item: (len(asus_mfg_fields(item[1])), sum(map(len, asus_mfg_fields(item[1]).values()))),
        )
        fields = asus_mfg_fields(best_block)
        items = []
        for label, start, end in ASUS_MFG_SLOTS:
            if label not in fields:
                continue
            value = fields[label]
            key = (label, value)
            if key in seen:
                continue
            seen.add(key)
            items.append(LenovoDmiItem(label, value, best_offset + start, best_offset + end))
        if "Model Identifier" in fields and all(item.label != "Model Identifier" for item in items):
            value = fields["Model Identifier"]
            key = ("Model Identifier", value)
            if key not in seen:
                seen.add(key)
                items.append(LenovoDmiItem("Model Identifier", value, best_offset, best_offset + len(best_block)))
        if items:
            groups.append((best_offset, best_offset + len(best_block), items))
    for candidate in find_winkeys(buffer):
        classification = re.sub(r"^likely\s+", "", candidate.classification, flags=re.IGNORECASE)
        value = f"{candidate.key} | {classification}" if classification else candidate.key
        key = ("Windows Product Key", value)
        if key in seen:
            continue
        seen.add(key)
        start = candidate.offset & ~(0x1000 - 1)
        end = min(len(buffer), start + 0x1000)
        groups.append((start, end, [LenovoDmiItem("Windows Product Key", value, candidate.offset, candidate.offset + len(candidate.key))]))
    return groups


def asus_dmi_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks = []
    for offset in find_all_bytes(buffer, ASUS_MFG_MARKER):
        block = buffer[offset:offset + ASUS_DMI_RECORD_SIZE]
        if len(block) != ASUS_DMI_RECORD_SIZE:
            continue
        fields = asus_mfg_fields(block)
        serial = fields.get("Board Serial Number", "")
        part = fields.get("Board Part Number", "")
        board_id = fields.get("Board ID", "")
        identifier = fields.get("Model Identifier", "")
        config = fields.get("Configuration ID", "")
        if not ASUS_SERIAL_PATTERN.fullmatch(serial):
            continue
        if not ASUS_BOARD_PART_PATTERN.fullmatch(part):
            continue
        if board_id and not ASUS_BOARD_ID_PATTERN.fullmatch(board_id):
            continue
        if identifier and not ASUS_MODEL_PATTERN.fullmatch(identifier):
            continue
        if not identifier and not (config and re.fullmatch(r"[A-Z0-9]{3,16}\.\d{1,4}|[A-Z0-9]{4,8}", config, re.IGNORECASE)):
            continue
        blocks.append((offset, block))
    return blocks


def asus_dmi_package_blocks(buffer: bytes) -> list[tuple[int, bytes]]:
    blocks = asus_dmi_blocks(buffer)
    used_ranges = [(offset, offset + len(block)) for offset, block in blocks]
    for candidate in find_winkeys(buffer):
        start = candidate.offset & ~(0x1000 - 1)
        end = min(len(buffer), start + 0x1000)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        block = buffer[start:end]
        used_ranges.append((start, end))
        blocks.append((start, block))
    return blocks


def asus_dmi_export_name(source: Path) -> Path:
    return unique_output_path(source.with_name(f"{source.stem}_ASUS_DMI.asusdmi"))


def asus_dmi_import_output_name(target: Path) -> Path:
    suffix = target.suffix or ".bin"
    return unique_output_path(target.with_name(f"{target.stem}_ASUS_DMI{suffix}"))


def export_asus_dmi(source: Path) -> tuple[Path, int]:
    blocks = asus_dmi_package_blocks(source.read_bytes())
    if not blocks:
        raise RuntimeError("Asus DMI block was not found.")
    payload = dmi_package_payload("ASUS", blocks)
    output = asus_dmi_export_name(source)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output, len(blocks)


def add_unique_dmi_item(items: list[LenovoDmiItem], seen: set[tuple[str, str]], label: str, value: str) -> None:
    value = value.strip()
    key = (label, value)
    if value and key not in seen:
        seen.add(key)
        items.append(LenovoDmiItem(label, value))


def is_hp_feature_byte(value: str) -> bool:
    return (
        20 <= len(value) <= 120
        and any(marker in value for marker in ("Wa", "apa", "Udp", "#S", "#D"))
        and not re.search(r"variable|lock|table|config|setup", value, re.IGNORECASE)
    )


def hp_preview_items(items: list[LenovoDmiItem]) -> list[LenovoDmiItem]:
    wanted = {"Model", "Serial Number", "Product ID", "Feature Byte", "Windows Product Key"}
    order = {
        "Model": 0,
        "Serial Number": 1,
        "Product ID": 2,
        "Feature Byte": 3,
        "Windows Product Key": 4,
    }
    filtered: list[LenovoDmiItem] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if item.label not in wanted:
            continue
        if item.label == "Serial Number" and not is_hp_serial_number(item.value):
            continue
        if item.label == "Feature Byte" and not is_hp_feature_byte(item.value):
            continue
        if item.label == "Model" and not is_hp_model(item.value):
            continue
        key = (item.label, item.value)
        if key in seen:
            continue
        seen.add(key)
        filtered.append(item)
    return sorted(filtered, key=lambda item: order.get(item.label, 99))


def is_hp_serial_number(value: str) -> bool:
    return (
        re.fullmatch(r"[A-Z0-9]{10,12}", value) is not None
        and value not in {"BuildId", "FactoryConfig", "HP_MUD"}
        and not re.search(r"setup|config|variable|build|factory", value, re.IGNORECASE)
    )


def is_hp_model(value: str) -> bool:
    lower = value.lower()
    if not value.startswith("HP "):
        return False
    if any(term in lower for term in ("linux installer", "firmware", "uefi", "diagnostic")):
        return False
    return True


def hp_mud_dmi_items(buffer: bytes) -> list[LenovoDmiItem]:
    blocks = hp_mud_blocks(buffer)
    if not blocks:
        return []
    items: list[LenovoDmiItem] = []
    seen: set[tuple[str, str]] = set()
    strings = utf16le_strings_with_offsets(blocks[0][1], blocks[0][0])
    values = [value for _offset, value in strings]
    for index, value in enumerate(values):
        if value == "HP_MUD" and index + 1 < len(values):
            candidate = values[index + 1]
            if is_hp_serial_number(candidate):
                add_unique_dmi_item(items, seen, "Serial Number", candidate)
        elif value == "BuildId" and index + 1 < len(values):
            continue
        elif value == "FactoryConfig" and index + 1 < len(values):
            add_unique_dmi_item(items, seen, "Feature Byte", values[index + 1])
    for value in values:
        if is_hp_model(value):
            add_unique_dmi_item(items, seen, "Model", value.removesuffix(" PC"))
        elif re.fullmatch(r"[A-Z0-9]{6,8}#[A-Z0-9]{3}", value):
            add_unique_dmi_item(items, seen, "Product ID", value)
        elif re.fullmatch(r"[A-Z0-9]{12,14}", value) and value.startswith("PK"):
            add_unique_dmi_item(items, seen, "CT Number", value)
        elif re.fullmatch(r"T\d{2}", value):
            add_unique_dmi_item(items, seen, "BIOS ID", value)
    if not any(item.label == "BIOS ID" for item in items):
        search_start = buffer.find(HP_MUD_MARKER)
        search_end = min(len(buffer), search_start + 0x100000) if search_start >= 0 else len(buffer)
        search_area = buffer[search_start:search_end] if search_start >= 0 else buffer
        for match in re.finditer(rb"(?<![A-Za-z0-9])T\d{2}(?![A-Za-z0-9])", search_area):
            value = match.group(0).decode("ascii")
            if value not in {"TLS"}:
                add_unique_dmi_item(items, seen, "BIOS ID", value)
                break
    for candidate in find_winkeys(buffer):
        add_unique_dmi_item(items, seen, "Windows Product Key", candidate.key)
        break
    return hp_preview_items(items)


def hp_legacy_dmi_items(buffer: bytes) -> list[LenovoDmiItem]:
    blocks = hp_legacy_dmi_blocks(buffer)
    if not blocks:
        return []
    items: list[LenovoDmiItem] = []
    seen: set[tuple[str, str]] = set()
    block = blocks[0][1]
    values = [
        match.group(0).decode("ascii", errors="ignore").strip()
        for match in re.finditer(rb"[ -~]{4,}", block)
    ]
    for value in values:
        if HP_INSYDE_DMI_MARKER.decode("ascii") in value:
            serial = value.split(HP_INSYDE_DMI_MARKER.decode("ascii"), 1)[0].strip()
            if re.fullmatch(r"[A-Z0-9]{10}", serial):
                add_unique_dmi_item(items, seen, "Serial Number", serial)
            continue
        if feature_match := re.search(r"([A-Za-z0-9.#]{30,80})", value):
            feature = feature_match.group(1)
            if is_hp_feature_byte(feature):
                add_unique_dmi_item(items, seen, "Feature Byte", feature)
                continue
        if re.fullmatch(r"[A-Z0-9]{10}", value):
            add_unique_dmi_item(items, seen, "Serial Number", value)
        elif is_hp_model(value) and ("Laptop" in value or "Book" in value or "Desk" in value or "Workstation" in value):
            add_unique_dmi_item(items, seen, "Model", value)
        elif re.fullmatch(r"[A-Z0-9]{5,8}#[A-Z0-9]{3}", value):
            add_unique_dmi_item(items, seen, "Product ID", value)
        elif re.fullmatch(r"20\d{2}", value):
            continue
        elif re.fullmatch(r"[A-Za-z0-9.#]{30,80}", value) and is_hp_feature_byte(value):
            add_unique_dmi_item(items, seen, "Feature Byte", value)
        elif value.startswith(("14WW", "15WW", "16WW", "17WW", "18WW")) and "#" in value:
            continue
        elif re.fullmatch(r"[A-Z0-9]{12,16}", value):
            continue
        elif WINKEY_PATTERN.fullmatch(value.encode("ascii", errors="ignore")):
            add_unique_dmi_item(items, seen, "Windows Product Key", value)
    return hp_preview_items(items)


def find_hp_dmi(buffer: bytes) -> list[LenovoDmiItem]:
    items: list[LenovoDmiItem] = []
    for _start, _end, block_items in find_hp_dmi_groups(buffer):
        items.extend(block_items)
    return items


def find_hp_dmi_groups(buffer: bytes) -> list[tuple[int, int, list[LenovoDmiItem]]]:
    groups: list[tuple[int, int, list[LenovoDmiItem]]] = []
    legacy_items = hp_legacy_dmi_items(buffer)
    if legacy_items:
        start, block = hp_legacy_dmi_blocks(buffer)[0]
        groups.append((start, start + len(block), legacy_items))
        append_hp_winkey_groups(buffer, groups)
        return groups
    mud_items = hp_mud_dmi_items(buffer)
    if mud_items:
        start, block = hp_mud_blocks(buffer)[0]
        groups.append((start, start + len(block), mud_items))
        append_hp_winkey_groups(buffer, groups)
        return groups
    items: list[LenovoDmiItem] = []
    seen = set()
    for offset, block in hp_dmi_blocks(buffer):
        block_items: list[LenovoDmiItem] = []
        for match in re.findall(rb"[ -~]{3,}", block):
            value = match.decode("ascii", errors="ignore").strip()
            if not value or value in seen:
                continue
            lower = value.lower()
            if not (
                value.startswith(("HP", "Hewlett"))
                or "serial" in lower
                or "product" in lower
                or "sku" in lower
                or "feature" in lower
                or "build" in lower
                or "system board" in lower
                or WINKEY_PATTERN.fullmatch(value.encode("ascii", errors="ignore"))
            ):
                continue
            seen.add(value)
            block_items.append(LenovoDmiItem(hp_dmi_label(value), value))
        block_items = hp_preview_items(block_items)
        if block_items:
            groups.append((offset, offset + len(block), block_items))
            items.extend(block_items)
    append_hp_winkey_groups(buffer, groups)
    return groups


def append_hp_winkey_groups(buffer: bytes, groups: list[tuple[int, int, list[LenovoDmiItem]]]) -> None:
    used_ranges = [(start, end) for start, end, _items in groups]
    seen_keys = {
        item.value.split(" | ", 1)[0]
        for _start, _end, items in groups
        for item in items
        if item.label == "Windows Product Key"
    }
    for candidate in find_winkeys(buffer):
        if candidate.key in seen_keys:
            continue
        classification = re.sub(r"^likely\s+", "", candidate.classification, flags=re.IGNORECASE)
        value = f"{candidate.key} | {classification}" if classification else candidate.key
        start = candidate.offset & ~(0x1000 - 1)
        end = min(len(buffer), start + 0x1000)
        if any(start >= used_start and end <= used_end for used_start, used_end in used_ranges):
            continue
        groups.append((start, end, [LenovoDmiItem("Windows Product Key", value, candidate.offset, candidate.offset + len(candidate.key))]))


def find_winkeys(buffer: bytes) -> list[WinKeyCandidate]:
    by_offset: dict[int, WinKeyCandidate] = {}
    if len(buffer) < WINKEY_LENGTH:
        return []
    for marker_offset in find_all_bytes(buffer, WINKEY_OEM_MARKER):
        add_winkey_range_matches(buffer, marker_offset + len(WINKEY_OEM_MARKER), 256, "Hex marker", by_offset)
    for marker_offset in find_all_bytes(buffer, b"MSDM"):
        add_winkey_range_matches(buffer, marker_offset, 512, "ACPI MSDM", by_offset)
    add_lenovo_lenv_matches(buffer, by_offset)
    for anchor in WINKEY_ANCHORS:
        for marker_offset in find_all_bytes(buffer, anchor.encode("ascii")):
            add_winkey_range_matches(buffer, marker_offset, 768, f"Near {anchor}", by_offset)
    add_winkey_range_matches(buffer, 0, len(buffer), "Direct pattern", by_offset)
    return sorted(by_offset.values(), key=lambda candidate: (method_priority(candidate.method), candidate.offset))


def score_rgn(input_info: FirmwareInfo, candidate: Path) -> tuple[float, str]:
    c = parse_filename_info(candidate)
    if input_info.major is None or input_info.minor is None:
        return 0, "missing-input-version"
    if c.major != input_info.major or c.minor != input_info.minor:
        return 0, "version-mismatch"
    score = 0
    reasons = []
    score += 150
    reasons.extend(["major", "minor"])
    input_sku = normalize_sku(input_info.sku)
    cand_sku = normalize_sku(c.sku)
    if input_sku:
        if not sku_matches(input_sku, cand_sku):
            return 0, "sku-mismatch"
        input_family, input_platform = sku_key(input_sku)
        candidate_family, candidate_platform = sku_key(cand_sku)
        if input_family and candidate_family == input_family:
            score += 50
            reasons.append("sku-family")
        if input_platform and candidate_platform == input_platform:
            score += 45
            reasons.append("platform")
        elif input_platform and candidate_platform:
            return 0, "platform-mismatch"
    else:
        return 0, "missing-input-sku"
    name_lower = candidate.name.lower()
    if "rgn" in name_lower:
        score += 80
        reasons.append("rgn")
    elif "extr" in name_lower:
        score -= 120
        reasons.append("extracted")
    else:
        score += 35
        reasons.append("region-like")
    if "prd" in name_lower:
        score += 25
        reasons.append("prd")
    cv = version_tuple(c.version)
    iv = version_tuple(input_info.version)
    if cv == iv:
        score += 1000
        reasons.append("exact-version")
        return score, ",".join(reasons)
    distance = abs(version_rank(c.version) - version_rank(input_info.version))
    score += max(0, 100 - distance / 10_000)
    reasons.append("nearest-version")
    return score, ",".join(reasons)


def find_best_rgn(repo: Path, input_info: FirmwareInfo) -> tuple[Path, list[dict]]:
    candidates = [
        p for p in repo.rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".bin", ".rgn"}
        and (input_info.version or "").split(".", 1)[0] in p.name
    ]
    ranked = []
    for p in candidates:
        score, reason = score_rgn(input_info, p)
        if score:
            ranked.append({"path": str(p), "score": score, "reason": reason})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    if not ranked:
        raise FileNotFoundError("No matching ME Region firmware found in ME Region root.")
    best = Path(ranked[0]["path"])
    if ranked[0]["score"] < 170:
        raise RuntimeError(
            "Best RGN candidate is weak. Refusing automatic selection; inspect manifest.json."
        )
    return best, ranked[:20]


def candidate_label(path: Path) -> str:
    return path.name


def fitc_label(path: Path) -> str:
    return path.name


def find_fitc(fitc_root: Path, major: int | None) -> Path | None:
    candidates = find_fitc_candidates(fitc_root, major)
    return candidates[0] if candidates else None


def score_fitc(input_info: FirmwareInfo, candidate: Path) -> tuple[float, str]:
    major, minor, _hotfix, _build = version_tuple(str(candidate))
    target_version = input_info.fit or input_info.version
    target_major, target_minor, _target_hotfix, _target_build = version_tuple(target_version)
    score = 0
    reasons = []
    if target_major:
        if major != target_major:
            return 0, "major-mismatch"
        score += 150
        reasons.append("major")
    if target_minor and minor == target_minor:
        score += 80
        reasons.append("minor")
    candidate_version = version_tuple(str(candidate))
    if target_version and candidate_version == version_tuple(target_version):
        score += 500
        reasons.append("exact-fit-version")
        return score, ",".join(reasons)
    distance = abs(version_rank(str(candidate)) - version_rank(target_version))
    score += max(0, 100 - distance / 10_000)
    reasons.append("nearest-fit-version" if input_info.fit else "nearest-version")
    return score, ",".join(reasons)


def find_ranked_fitc_candidates(fitc_root: Path, input_info: FirmwareInfo) -> list[dict]:
    ranked = []
    target_version = input_info.fit or input_info.version
    target_major, target_minor, _target_hotfix, _target_build = version_tuple(target_version)
    candidates = find_fitc_candidates(fitc_root, target_major or input_info.major)
    if target_minor:
        same_minor = [path for path in candidates if version_tuple(str(path))[1] == target_minor]
        if same_minor:
            candidates = same_minor
    for path in candidates:
        score, reason = score_fitc(input_info, path)
        if score:
            ranked.append({"path": str(path), "score": score, "reason": reason})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def find_fitc_candidates(fitc_root: Path, major: int | None) -> list[Path]:
    names = ["fitc.exe", "fit.exe", "Flash Image Tool.exe"]
    found = find_files(fitc_root, names)
    found.extend(
        p for p in fitc_root.rglob("*.exe")
        if re.match(r"^\d+\.\d+(?:\.\d+){0,2}\.exe$", p.name, re.I)
    )
    found = sorted(set(found), key=lambda p: str(p).lower())
    if not found:
        return []
    if major is None:
        return found
    exact_name = [p for p in found if p.name.startswith(f"{major}.")]
    exact_folder = [p for p in found if any(part == str(major) or part.startswith(f"{major}.") for part in p.parts)]
    candidates = sorted(set(exact_name + exact_folder), key=lambda p: version_tuple(str(p)))
    return candidates or found


def copy_inputs(workdir: Path, image: Path, rgn: Path) -> tuple[Path, Path]:
    input_copy = workdir / "input_original.bin"
    rgn_copy = workdir / "ME Region.bin"
    shutil.copy2(image, input_copy)
    shutil.copy2(rgn, rgn_copy)
    return input_copy, rgn_copy


def fitc_output_text(result: dict) -> str:
    return "\n".join(
        [result.get("output", "") or ""]
        + [step.get("output", "") or "" for step in result.get("steps", [])]
    )


def fitc_failed_me_file_system(result: dict) -> str:
    output = fitc_output_text(result)
    for name in ("MFS", "EFS"):
        if f"Failed to initialize {name}" in output:
            return name
    return ""


def intel_flash_descriptor_region(buffer: bytes | bytearray, region_index: int, name: str) -> FlashRegion:
    if len(buffer) < 0x1000:
        raise RuntimeError("Input is too small to contain an Intel Flash Descriptor.")
    if buffer[0x10:0x14] != bytes.fromhex("5A A5 F0 0F"):
        raise RuntimeError("Intel Flash Descriptor signature was not found.")
    flmap0 = int.from_bytes(buffer[0x14:0x18], "little")
    frba = ((flmap0 >> 16) & 0xFF) << 4
    entry = frba + region_index * 4
    if entry + 4 > len(buffer):
        raise RuntimeError("Intel Flash Descriptor region table is out of range.")
    value = int.from_bytes(buffer[entry:entry + 4], "little")
    base = value & 0x0FFF
    limit = (value >> 16) & 0x0FFF
    if base == 0x0FFF or limit == 0 or limit < base:
        raise RuntimeError(f"Intel Flash Descriptor does not define a valid {name} region.")
    offset = base << 12
    end = (limit + 1) << 12
    if end > len(buffer):
        raise RuntimeError(f"{name} region is outside the input file.")
    return FlashRegion(name, offset, end - offset)


def create_me_fs_repaired_input(input_image: Path, rgn_image: Path, workdir: Path) -> tuple[Path, FlashRegion]:
    data = bytearray(input_image.read_bytes())
    rgn = rgn_image.read_bytes()
    me_region = intel_flash_descriptor_region(data, 2, "ME")
    start = me_region.offset
    end = start + len(rgn)
    if end > len(data):
        raise RuntimeError(
            f"Selected ME Region does not fit from BIOS ME offset ({len(rgn)} bytes at 0x{start:X} > BIOS size {len(data)} bytes)."
        )
    data[start:end] = rgn
    output = workdir / "input_me_fs_repaired.bin"
    output.write_bytes(data)
    return output, me_region


def maybe_run_fitc(fitc: Path, workdir: Path, input_image: Path, me_region: Path | None, output_image: Path) -> dict:
    help_code, help_out = run([str(fitc), "-?"], cwd=fitc.parent)
    result = {
        "fitc": str(fitc),
        "help_code": help_code,
        "help": help_out[:4000],
        "steps": [],
        "ran": False,
    }
    if "--decompose" in help_out:
        return maybe_run_modular_fitc(fitc, workdir, input_image, me_region, output_image, result)

    config_xml = workdir / "config.xml"
    save_cmd = [str(fitc), "-f", str(input_image), "-save", str(config_xml)]
    code, out = run(save_cmd, cwd=workdir)
    result["ran"] = True
    result["steps"].append({"name": "save_xml", "cmd": save_cmd, "code": code, "output": out})
    if code != 0 or not config_xml.exists():
        result["code"] = code
        result["output"] = out
        return result

    build_cmd = [str(fitc), "-b", "-f", str(config_xml)]
    if me_region is not None:
        build_cmd.extend(["-me", str(me_region)])
    build_cmd.extend(["-o", str(output_image)])
    code, out = run(build_cmd, cwd=workdir)
    result["steps"].append({"name": "build_image", "cmd": build_cmd, "code": code, "output": out})
    result["code"] = code
    result["output"] = out
    return result


def fitc_succeeded(result: dict, output_image: Path) -> bool:
    return result.get("code") == 0 and output_image.exists()


def summarize_fitc_failure(fitc_runs: list[dict]) -> str:
    if not fitc_runs:
        return ""
    combined_output = "\n".join(
        (run_result.get("output", "") or "")
        + "\n"
        + "\n".join((step.get("output", "") or "") for step in run_result.get("steps", []))
        for run_result in fitc_runs
    )
    fitc_versions = [Path(run_result.get("fitc", "")).name for run_result in fitc_runs if run_result.get("fitc")]
    source_versions = []
    for pattern in (
        r"FIT version used to build the image: ([^\n]+)",
        r"MFIT version used to build the image: ([^\n]+)",
    ):
        source_versions.extend(v.strip() for v in re.findall(pattern, combined_output))
    failed_fs = next((name for name in ("MFS", "EFS") if f"Failed to initialize {name}" in combined_output), "")
    if failed_fs:
        lines = [
            f"FIT could not initialize ME {failed_fs}.",
            "Auto repair was attempted by replacing the BIOS ME region with the selected RGN when the Intel Flash Descriptor was readable.",
        ]
        repair_errors = [
            value.strip()
            for value in re.findall(r"ME file system repair retry was skipped: ([^\n]+)", combined_output)
            if value.strip()
        ]
        if repair_errors:
            lines.append("Repair retry failed: " + repair_errors[-1])
        return "\n".join(lines)
    invalid_size = re.findall(r"FIT output has invalid size \(([^\n]+)\)", combined_output)
    if invalid_size:
        return "FIT built an output with the wrong size: " + invalid_size[-1]
    if "Failed to parse CSE region" in combined_output:
        lines = ["FIT could not parse the CSE region in this dump."]
        if source_versions:
            lines.append(f"The dump was built with FIT: {source_versions[-1]}.")
        if fitc_versions:
            lines.append("Tried FIT: " + ", ".join(fitc_versions) + ".")
        lines.append("Add a FIT version closer to the dump version, or check whether the dump/CSE region is damaged.")
        return "\n".join(lines)
    if "Unknown container path" in combined_output:
        lines = ["The current FIT version is too old to read this dump layout."]
        if source_versions:
            lines.append(f"The dump was built with FIT: {source_versions[-1]}.")
        if fitc_versions:
            lines.append("Tried FIT: " + ", ".join(fitc_versions) + ".")
        lines.append("Try a newer FIT from the same major version.")
        return "\n".join(lines)
    if "Failed to load input file. Invalid input file type" in combined_output:
        lines = [
            "FIT could not load the selected input file.",
            "Possible causes:",
            "- The BIOS dump is not a full SPI/programmer dump.",
            "- The file is an extracted ME Region, OEM capsule, or unsupported format.",
            "- The selected FIT versions are not compatible with this BIOS.",
            "What to try:",
            "- Use a full SPI/programmer BIOS dump.",
            "- Add or choose a FIT version closer to the detected ME/FIT version.",
        ]
        if source_versions:
            lines.append(f"- This dump reports it was built with FIT: {source_versions[-1]}.")
        if fitc_versions:
            lines.extend(["FIT versions tried:"])
            lines.extend(f"- {version}" for version in fitc_versions)
        return "\n".join(lines)
    chunks = []
    for run_result in fitc_runs:
        fitc_name = Path(run_result.get("fitc", "")).name
        output = run_result.get("output", "") or ""
        for step in run_result.get("steps", []):
            output += "\n" + (step.get("output", "") or "")
        lines = []
        for pattern in (
            r"Error \d+: [^\n]+",
            r"ERROR\s+: [^\n]+",
            r"Details: [^\n]+",
            r"FIT version used to build the image: [^\n]+",
            r"MFIT version used to build the image: [^\n]+",
        ):
            for match in re.findall(pattern, output):
                clean = match.strip()
                clean = re.sub(r"\s+Cannot enable pre-lock with Delayed Authentication Mode enabled\.?", "", clean)
                if clean and clean not in lines:
                    lines.append(clean)
        if lines:
            chunks.append(f"{fitc_name}: {lines[0]}")
    return "\n".join(chunks[-4:])


def maybe_run_modular_fitc(
    fitc: Path,
    workdir: Path,
    input_image: Path,
    me_region: Path | None,
    output_image: Path,
    result: dict,
) -> dict:
    config_xml = workdir / "config.xml"
    clean_config_xml = workdir / "config_clean.xml"
    decompose_cmd = [str(fitc), "--decompose", str(input_image), "--saveconfig", str(config_xml)]
    code, out = run(decompose_cmd, cwd=workdir)
    result["ran"] = True
    result["steps"].append({"name": "decompose_saveconfig", "cmd": decompose_cmd, "code": code, "output": out})
    if code != 0 or not config_xml.exists():
        result["code"] = code
        result["output"] = out
        return result

    build_config_xml = config_xml
    if me_region is not None:
        patch_modular_config_me_region(config_xml, clean_config_xml, me_region)
        build_config_xml = clean_config_xml
    build_cmd = [str(fitc), "--loadconfig", str(build_config_xml), "--build", str(output_image)]
    code, out = run(build_cmd, cwd=workdir)
    result["steps"].append({"name": "build_image", "cmd": build_cmd, "code": code, "output": out})
    result["code"] = code
    result["output"] = out
    return result


def patch_modular_config_me_region(config_xml: Path, out_xml: Path, me_region: Path) -> None:
    tree = ET.parse(config_xml)
    root = tree.getroot()
    target = None
    for elem in root.iter():
        if elem.tag == "MeRegionFile" or elem.attrib.get("key") == "CsePlugin:CseRegion:MeRegionFile":
            target = elem
            break
    if target is None:
        raise RuntimeError("FIT modular XML does not contain CsePlugin:CseRegion:MeRegionFile.")
    target.set("value", str(me_region))
    tree.write(out_xml, encoding="utf-8", xml_declaration=True)


def find_built_image(workdir: Path, fitc: Path | None) -> Path | None:
    names = ["outimage.bin", "outimage.bin.bin", "intermediate.bin"]
    roots = [workdir]
    if fitc:
        roots.append(fitc.parent)
    for root in roots:
        if not root.exists():
            continue
        for name in names:
            direct = root / name
            if direct.exists() and direct.is_file():
                return direct
        for path in root.rglob("*"):
            if path.is_file() and path.name.lower() in names:
                return path
    return None


def valid_built_image(path: Path | None, expected_size: int) -> Path | None:
    if path and path.exists() and path.is_file() and path.stat().st_size == expected_size:
        return path
    return None


def note_invalid_built_image(result: dict, path: Path | None, expected_size: int) -> None:
    if not path or not path.exists() or not path.is_file():
        return
    actual_size = path.stat().st_size
    if actual_size == expected_size:
        return
    message = f"FIT output has invalid size ({actual_size} != {expected_size} bytes): {path}"
    result["invalid_output"] = {
        "path": str(path),
        "actual_size": actual_size,
        "expected_size": expected_size,
    }
    result["output"] = ((result.get("output") or "") + "\n" + message).strip()


def publish_clearme_output(source: Path, original: Path, out_root: Path) -> Path:
    suffix = original.suffix or ".bin"
    final = out_root / f"{original.stem}_CLEARME{suffix}"
    counter = 2
    while final.exists():
        final = out_root / f"{original.stem}_CLEARME_{counter}{suffix}"
        counter += 1
    shutil.copy2(source, final)
    return final


def clearme_output_name(original: Path, out_root: Path) -> Path:
    suffix = original.suffix or ".bin"
    final = out_root / f"{original.stem}_CLEARME{suffix}"
    counter = 2
    while final.exists():
        final = out_root / f"{original.stem}_CLEARME_{counter}{suffix}"
        counter += 1
    return final


def parse_size(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(kb|mb|gb|b)?", text)
    if not m:
        raise ValueError(f"Invalid size: {value}. Use values like 8MB, 16MB, or bytes.")
    number = float(m.group(1))
    unit = m.group(2) or "b"
    scale = {"b": 1, "kb": 1024, "mb": 1024 * 1024, "gb": 1024 * 1024 * 1024}[unit]
    size = int(number * scale)
    if size <= 0:
        raise ValueError("Size must be greater than zero.")
    return size


def parse_bios_mb_size(value: str | None) -> int:
    if not value or not value.strip():
        raise ValueError("BIOS size is required.")
    text = value.strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        text += "mb"
    size = parse_size(text)
    if size is None:
        raise ValueError("BIOS size is required.")
    return size


VALID_BIOS_SIZES = [
    512 * 1024,        # 512 KB
    1 * 1024 * 1024,   # 1 MB
    2 * 1024 * 1024,   # 2 MB
    4 * 1024 * 1024,   # 4 MB
    8 * 1024 * 1024,   # 8 MB
    12 * 1024 * 1024,  # 12 MB (8 + 4)
    16 * 1024 * 1024,  # 16 MB
    20 * 1024 * 1024,  # 20 MB (16 + 4)
    24 * 1024 * 1024,  # 24 MB (16 + 8)
    32 * 1024 * 1024,  # 32 MB
    40 * 1024 * 1024,  # 40 MB (32 + 8)
    48 * 1024 * 1024,  # 48 MB (32 + 16)
    64 * 1024 * 1024,  # 64 MB
    128 * 1024 * 1024, # 128 MB
]


def trim_bios_data(data: bytes | bytearray, max_margin: int = 1048576) -> tuple[bytes | bytearray, int]:
    size = len(data)
    if size in VALID_BIOS_SIZES:
        return data, 0
    valid_targets = [s for s in VALID_BIOS_SIZES if s < size]
    if not valid_targets:
        return data, 0
    target_size = max(valid_targets)
    excess = size - target_size
    if excess <= max_margin:
        return data[:target_size], excess
    return data, 0


def infer_chip1_size(total: int) -> int:
    mb = 1024 * 1024
    table = {
        16 * mb: 8 * mb,
        24 * mb: 16 * mb,
        32 * mb: 16 * mb,
        48 * mb: 32 * mb,
        64 * mb: 32 * mb,
    }
    if total not in table:
        raise ValueError(
            f"Cannot infer chip 1 size for merged image size {total} bytes. Enter it manually, for example 8MB or 16MB."
        )
    return table[total]


def unique_output_path(base: Path) -> Path:
    if not base.exists():
        return base
    counter = 2
    while True:
        candidate = base.with_name(f"{base.stem}_{counter}{base.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def asus_unlock_output_name(source: Path) -> Path:
    suffix = source.suffix or ".bin"
    return unique_output_path(source.with_name(f"{source.stem}_UNLOCK{suffix}"))


def dell_8fc8_unlock_output_name(source: Path) -> Path:
    suffix = source.suffix or ".bin"
    return unique_output_path(source.with_name(f"{source.stem}_UNLOCKED{suffix}"))


def has_password_payload(region: bytes | bytearray) -> bool:
    return any(value not in {0x00, 0xFF} for value in region)


def unlock_asus_password(source: Path, zero_length: int = ASUS_UNLOCK_ZERO_LENGTH) -> tuple[Path | None, list[tuple[int, int]]]:
    data = bytearray(source.read_bytes())
    cleared_ranges: list[tuple[int, int]] = []
    markers_found = 0
    marker_offset = data.find(ASUS_AMITSE_MARKER)
    while marker_offset >= 0:
        markers_found += 1
        start = marker_offset + len(ASUS_AMITSE_MARKER)
        end = min(start + zero_length, len(data))
        if start < end and has_password_payload(data[start:end]):
            data[start:end] = b"\x00" * (end - start)
            cleared_ranges.append((start, end - start))
        marker_offset = data.find(ASUS_AMITSE_MARKER, marker_offset + len(ASUS_AMITSE_MARKER))
    if not markers_found:
        raise RuntimeError("Password marker was not found. This file may not use the supported ASUS password layout.")
    if not cleared_ranges:
        return None, []
    output = asus_unlock_output_name(source)
    output.write_bytes(data)
    return output, cleared_ranges


def zero_after_markers(data: bytearray, marker: bytes, start_delta: int, zero_length: int) -> list[tuple[int, int]]:
    cleared_ranges: list[tuple[int, int]] = []
    marker_offset = data.find(marker)
    while marker_offset >= 0:
        start = marker_offset + start_delta
        end = min(start + zero_length, len(data))
        if start < end and has_password_payload(data[start:end]):
            data[start:end] = b"\x00" * (end - start)
            cleared_ranges.append((start, end - start))
        marker_offset = data.find(marker, marker_offset + len(marker))
    return cleared_ranges


def count_markers(data: bytearray, marker: bytes) -> int:
    count = 0
    marker_offset = data.find(marker)
    while marker_offset >= 0:
        count += 1
        marker_offset = data.find(marker, marker_offset + len(marker))
    return count


def unlock_acer_password(source: Path) -> tuple[Path | None, list[tuple[int, int]]]:
    data = bytearray(source.read_bytes())
    markers_found = count_markers(data, ACER_OLD_PASSWORD_MARKER) + count_markers(data, ACER_NEW_PASSWORD_MARKER)
    cleared_ranges = []
    cleared_ranges.extend(zero_after_markers(
        data,
        ACER_OLD_PASSWORD_MARKER,
        ACER_OLD_PASSWORD_OFFSET,
        ACER_OLD_UNLOCK_ZERO_LENGTH,
    ))
    cleared_ranges.extend(zero_after_markers(
        data,
        ACER_NEW_PASSWORD_MARKER,
        len(ACER_NEW_PASSWORD_MARKER),
        ACER_NEW_UNLOCK_ZERO_LENGTH,
    ))
    if not markers_found:
        raise RuntimeError("Password marker was not found. This file may not use the supported ACER password layout.")
    if not cleared_ranges:
        return None, []
    output = asus_unlock_output_name(source)
    output.write_bytes(data)
    return output, cleared_ranges


def hp_unlock_region_length(section: bytes | bytearray) -> int:
    if not all(marker in section for marker in HP_UNLOCK_REQUIRED_MARKERS):
        return 0
    if not any(marker in section for marker in HP_UNLOCK_OPTIONAL_MARKERS):
        return 0
    last_used = -1
    for index, value in enumerate(section):
        if value != 0xFF:
            last_used = index
    return last_used + 1 if last_used >= len(HP_NVRAM_ACTIVE_MARKER) else 0


def hp_ec_unlock_region_length(section: bytes | bytearray) -> int:
    if not all(marker in section for marker in HP_EC_UNLOCK_REQUIRED_MARKERS):
        return 0
    last_used = -1
    for index, value in enumerate(section):
        if value != 0xFF:
            last_used = index
    return last_used + 1 if last_used >= len(HP_NVRAM_ACTIVE_MARKER) else 0


def unlock_hp_password(source: Path) -> tuple[Path | None, list[tuple[int, int]]]:
    data = bytearray(source.read_bytes())
    cleared_ranges: list[tuple[int, int]] = []
    markers_found = 0
    marker_offset = data.find(HP_NVRAM_ACTIVE_MARKER)
    while marker_offset >= 0:
        markers_found += 1
        end = min(marker_offset + HP_UNLOCK_SCAN_SIZE, len(data))
        clear_length = hp_unlock_region_length(data[marker_offset:end])
        if not clear_length:
            clear_length = hp_ec_unlock_region_length(data[marker_offset:end])
        if clear_length:
            data[marker_offset:marker_offset + clear_length] = b"\xFF" * clear_length
            cleared_ranges.append((marker_offset, clear_length))
        marker_offset = data.find(HP_NVRAM_ACTIVE_MARKER, marker_offset + len(HP_NVRAM_ACTIVE_MARKER))
    if not markers_found or not cleared_ranges:
        return None, []
    output = asus_unlock_output_name(source)
    output.write_bytes(data)
    return output, cleared_ranges


def unlock_dell_8fc8_password(source: Path) -> tuple[Path | None, list[tuple[int, int]]]:
    data = bytearray(source.read_bytes())
    cleared_ranges: list[tuple[int, int]] = []
    signatures_found = 0
    for signature in DELL_8FC8_UNLOCK_SIGNATURES:
        marker_offset = data.find(signature)
        while marker_offset >= 0:
            signatures_found += 1
            patch_offset = marker_offset + 2
            if data[patch_offset] == 0xAA:
                data[patch_offset] = 0x00
                cleared_ranges.append((patch_offset, 1))
            marker_offset = data.find(signature, marker_offset + len(signature))
    if not signatures_found:
        if any(signature in data for signature in DELL_8FC8_UNLOCKED_SIGNATURES):
            return None, []
        raise RuntimeError("Dell 8FC8 lock signature was not found. This file may not use the supported 8FC8 layout.")
    if not cleared_ranges:
        return None, []
    output = dell_8fc8_unlock_output_name(source)
    output.write_bytes(data)
    return output, cleared_ranges


def clearme_name_for(source: Path, out_root: Path) -> Path:
    suffix = source.suffix or ".bin"
    return unique_output_path(out_root / f"{source.stem}_CLEARME{suffix}")


def split_dual_output(
    merged: Path,
    original: Path,
    chip1_size: int,
    keep_merged: bool = True,
    file1_original: Path | None = None,
    file2_original: Path | None = None,
) -> tuple[Path, Path]:
    total = merged.stat().st_size
    if chip1_size >= total:
        raise ValueError(f"Chip 1 size {chip1_size} must be smaller than merged image size {total}.")
    data = merged.read_bytes()
    if file1_original and file2_original:
        chip1 = clearme_name_for(file1_original, merged.parent)
        chip2 = clearme_name_for(file2_original, merged.parent)
    else:
        chip1 = unique_output_path(merged.with_name(f"{original.stem}_CHIP1_CLEARME{original.suffix or '.bin'}"))
        chip2 = unique_output_path(merged.with_name(f"{original.stem}_CHIP2_CLEARME{original.suffix or '.bin'}"))
    chip1.write_bytes(data[:chip1_size])
    chip2.write_bytes(data[chip1_size:])
    if not keep_merged:
        merged.unlink()
    return chip1, chip2


def merge_dual_inputs(file1: Path, file2: Path, out_root: Path) -> tuple[Path, int]:
    if not file1.exists():
        raise FileNotFoundError(f"Dual BIOS file 1 not found: {file1}")
    if not file2.exists():
        raise FileNotFoundError(f"Dual BIOS file 2 not found: {file2}")
    
    data1, excess1 = trim_bios_data(file1.read_bytes())
    if excess1 > 0:
        print(f"[INFO] Cleared {excess1} bytes trailing metadata from {file1.name} (new size: {len(data1)} bytes).", flush=True)

    data2, excess2 = trim_bios_data(file2.read_bytes())
    if excess2 > 0:
        print(f"[INFO] Cleared {excess2} bytes trailing metadata from {file2.name} (new size: {len(data2)} bytes).", flush=True)

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    merged = out_root / f"MERGED_{stamp}.bin"
    with merged.open("wb") as out:
        out.write(data1)
        out.write(data2)
    return merged, len(data1)


def bios_size_label(size: int) -> str:
    mb = 1024 * 1024
    if size % mb == 0:
        return f"{size // mb}MB"
    return f"{size}B"


def merged_bios_output_name(total_size: int, out_root: Path) -> Path:
    return unique_output_path(out_root / f"{bios_size_label(total_size)}_MERGED.bin")


def split_bios_output_names(source: Path, out_root: Path) -> tuple[Path, Path]:
    suffix = source.suffix or ".bin"
    chip1 = unique_output_path(out_root / f"{source.stem}_BIOS1{suffix}")
    chip2 = unique_output_path(out_root / f"{source.stem}_BIOS2{suffix}")
    return chip1, chip2


def merge_bios_files(file1: Path, file2: Path, out_root: Path | None = None) -> tuple[Path, list[tuple[Path, int, int]]]:
    output_root = out_root or file1.parent
    output_root.mkdir(parents=True, exist_ok=True)
    data1, excess1 = trim_bios_data(file1.read_bytes())
    data2, excess2 = trim_bios_data(file2.read_bytes())
    output = merged_bios_output_name(len(data1) + len(data2), output_root)
    output.write_bytes(bytes(data1) + bytes(data2))
    return output, [(file1, len(data1), excess1), (file2, len(data2), excess2)]


def split_bios_file(source: Path, bios1_size: int, bios2_size: int, out_root: Path | None = None) -> tuple[Path, Path, int]:
    output_root = out_root or source.parent
    output_root.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    expected_size = bios1_size + bios2_size
    excess = 0
    if len(data) != expected_size:
        trimmed, trimmed_excess = trim_bios_data(data)
        if len(trimmed) == expected_size:
            data = bytes(trimmed)
            excess = trimmed_excess
    if len(data) != expected_size:
        raise ValueError(
            f"Input size {len(data)} bytes does not match BIOS 1 + BIOS 2 size {expected_size} bytes."
        )
    chip1, chip2 = split_bios_output_names(source, output_root)
    chip1.write_bytes(data[:bios1_size])
    chip2.write_bytes(data[bios1_size:])
    return chip1, chip2, excess


def cleanup_job(workdir: Path) -> None:
    resolved_workdir = workdir.resolve()
    if resolved_workdir.exists() and resolved_workdir.is_dir():
        shutil.rmtree(resolved_workdir)


def write_manual_steps(workdir: Path, fitc: Path | None) -> None:
    text = f"""AUTO CLEAR ME - MANUAL FIT FALLBACK

FIT CLI was not confirmed, so finish these steps manually:

1. Open the input image in the matching FIT.
   FIT: {fitc or 'not found'}
   Input: {workdir / 'input_original.bin'}

2. Save configuration as:
   {workdir / 'config.xml'}

3. Open the created Decomp folder and replace its Engine file with:
   {workdir / 'ME Region.bin'}

4. Reopen FIT, File > Open the saved config.xml, then Build Image.

5. Copy the final outimage.bin into this work folder and run:
   python AutoClearME.py verify --input input_original.bin --output outimage.bin --mea PATH_TO_MEA

Important:
- For Engine-region-only input, final size must match original. Pad with 0xFF if FIT creates a smaller file.
- After flashing, run fpt -greset on the target system.
"""
    (workdir / "MANUAL_STEPS.txt").write_text(text, encoding="utf-8")


def load_config(path: str | None) -> dict:
    config_path = Path(path).resolve() if path else DEFAULT_CONFIG
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a JSON object: {config_path}")
    return data


def pick_arg(args: argparse.Namespace, config: dict, attr: str, key: str | None = None) -> str | None:
    value = getattr(args, attr, None)
    if value:
        return value
    return config.get(key or attr)


def require_config_values(**values: str | None) -> None:
    missing = [
        name for name, value in values.items()
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing config values: "
            + ", ".join(missing)
            + ". Edit config.json or pass them as command-line arguments."
        )


def log_path_name(path: Path | str) -> str:
    return Path(path).name or str(path)


def prepare_input(args: argparse.Namespace, out_value: str | None) -> PrepareInput:
    if args.dual_file1 and args.dual_file2:
        file1 = Path(args.dual_file1).resolve()
        file2 = Path(args.dual_file2).resolve()
        out_root = Path(out_value).resolve() if out_value else file1.parent
        out_root.mkdir(parents=True, exist_ok=True)
        temp_merged_input, merged_chip1_size = merge_dual_inputs(file1, file2, out_root)
        print(f"[0/5] Merged Dual BIOS files in user order: {log_path_name(file1)} + {log_path_name(file2)}", flush=True)
        return PrepareInput(
            image=temp_merged_input,
            out_root=out_root,
            source_image=temp_merged_input,
            temp_merged_input=temp_merged_input,
            merged_chip1_size=merged_chip1_size,
            dual_file1_original=file1,
            dual_file2_original=file2,
        )
    if not args.input:
        raise ValueError("Missing --input, or use --dual-file1 and --dual-file2 for Dual BIOS merge.")
    image = Path(args.input).resolve()
    out_root = Path(out_value).resolve() if out_value else image.parent
    out_root.mkdir(parents=True, exist_ok=True)

    data, excess = trim_bios_data(image.read_bytes())
    source_image = image
    temp_input_dir = None
    if excess > 0:
        print(f"[INFO] Cleared {excess} bytes trailing metadata from {image.name} (new size: {len(data)} bytes).", flush=True)
        temp_input_dir = Path(tempfile.mkdtemp(prefix="AutoClearME_trimmed_"))
        trimmed_image = temp_input_dir / image.name
        trimmed_image.write_bytes(data)
        image = trimmed_image

    return PrepareInput(image=image, out_root=out_root, source_image=source_image, temp_input_dir=temp_input_dir)


def resolve_mea(mea_value: str | None, repo: Path, fitc_root: Path) -> Path | None:
    configured_mea = Path(mea_value).resolve() if mea_value else None
    search_roots = [configured_mea.parent] if configured_mea else [repo.parent, fitc_root.parent, Path.cwd()]
    return configured_mea if configured_mea and configured_mea.exists() else find_me_analyzer(search_roots)


def cached_firmware_info(args: argparse.Namespace) -> FirmwareInfo | None:
    if args.detected_version:
        info = FirmwareInfo(
            version=args.detected_version,
            sku=args.detected_sku or "",
            type=args.detected_type or "",
            data_state=args.detected_data_state or "",
        )
        version_match = VERSION_RE.search(info.version)
        if version_match:
            info.major = int(version_match.group("major"))
            info.minor = int(version_match.group("minor"))
        return info
    return None


def prepare_firmware_info(args: argparse.Namespace, image: Path, mea: Path | None) -> FirmwareInfo:
    info = cached_firmware_info(args)
    if info:
        print(f"[1/5] Using cached ME Analyzer result: {log_path_name(image)}...", flush=True)
        return info
    print(f"[1/5] Analyzing input with ME Analyzer: {log_path_name(image)}...", flush=True)
    if not mea:
        raise FileNotFoundError("ME Analyzer not found. Pass --mea path/to/MEA.py or MEA.exe.")
    return analyze_with_mea(mea, image)


def matching_rgn(args: argparse.Namespace, repo: Path, info: FirmwareInfo) -> tuple[Path, list[dict]]:
    print(f"[3/5] Searching matching ME Region in: {log_path_name(repo)}...", flush=True)
    if args.rgn:
        rgn = Path(args.rgn).resolve()
        if not rgn.exists():
            raise FileNotFoundError(f"Selected ME Region was not found: {rgn}")
        return rgn, [{"path": str(rgn), "score": 999, "reason": "user-selected"}]
    return find_best_rgn(repo, info)


def ranked_fit_candidates(args: argparse.Namespace, fitc_root: Path, info: FirmwareInfo) -> list[Path]:
    candidates = [Path(item["path"]) for item in find_ranked_fitc_candidates(fitc_root, info)]
    if args.fitc:
        selected_fitc = Path(args.fitc).resolve()
        candidates = [selected_fitc, *[p for p in candidates if p.resolve() != selected_fitc]]
    return [p for p in candidates if p.exists()]


def split_cleared_output(
    args: argparse.Namespace,
    published_output: Path,
    prep: PrepareInput,
) -> list[str]:
    chip1_size = (
        prep.merged_chip1_size
        if prep.merged_chip1_size
        else infer_chip1_size(published_output.stat().st_size)
        if (args.chip1_size or "").strip().lower() in {"", "auto"}
        else parse_size(args.chip1_size)
    )
    return [
        str(p)
        for p in split_dual_output(
            published_output,
            prep.image,
            chip1_size,
            keep_merged=False,
            file1_original=prep.dual_file1_original,
            file2_original=prep.dual_file2_original,
        )
    ]


def try_fit_build(
    args: argparse.Namespace,
    prep: PrepareInput,
    workdir: Path,
    input_copy: Path,
    rgn_copy: Path,
    fitc_candidates: list[Path],
) -> BuildResult:
    fitc = fitc_candidates[0] if fitc_candidates else None
    published_output = None
    split_outputs: list[str] = []
    fitc_runs: list[dict] = []
    if not args.try_fitc or not fitc:
        print(f"[5/5] Job prepared. FIT CLI build was not requested or FIT was not found.", flush=True)
        return BuildResult(None, [], [], fitc)

    for idx, candidate_fitc in enumerate(fitc_candidates, 1):
        print(f"[5/5] Trying FIT CLI build ({idx}/{len(fitc_candidates)}): {fitc_label(candidate_fitc)}...", flush=True)
        output_source = prep.source_image or prep.image
        expected_output_size = output_source.stat().st_size
        work_output = clearme_output_name(output_source, workdir)
        fitc_result = maybe_run_fitc(candidate_fitc, workdir, input_copy, rgn_copy, work_output)
        fitc_runs.append(fitc_result)
        (workdir / "fitc_run.json").write_text(json.dumps(fitc_runs, indent=2), encoding="utf-8")
        built_candidate = work_output if work_output.exists() else find_built_image(workdir, candidate_fitc)
        note_invalid_built_image(fitc_result, built_candidate, expected_output_size)
        built = valid_built_image(built_candidate, expected_output_size)
        failed_fs = fitc_failed_me_file_system(fitc_result)
        if not built and failed_fs:
            print(f"[5/5] FIT could not initialize {failed_fs}. Auto repair the ME Region and retrying FIT...", flush=True)
            retry_output = clearme_output_name(output_source, workdir)
            try:
                repaired_input, me_region = create_me_fs_repaired_input(input_copy, rgn_copy, workdir)
                retry_result = maybe_run_fitc(candidate_fitc, workdir, repaired_input, None, retry_output)
                retry_result["me_fs_repair"] = {
                    "failed_fs": failed_fs,
                    "input": str(repaired_input),
                    "me_offset": me_region.offset,
                    "me_size": me_region.size,
                    "rgn_size": rgn_copy.stat().st_size,
                }
            except Exception as exc:
                retry_result = {
                    "fitc": str(candidate_fitc),
                    "ran": False,
                    "code": 2,
                    "output": f"ME file system repair retry was skipped: {exc}",
                    "steps": [],
                    "me_fs_repair": {"failed_fs": failed_fs, "error": str(exc)},
                }
            fitc_runs.append(retry_result)
            (workdir / "fitc_run.json").write_text(json.dumps(fitc_runs, indent=2), encoding="utf-8")
            built_candidate = retry_output if retry_output.exists() else find_built_image(workdir, candidate_fitc)
            note_invalid_built_image(retry_result, built_candidate, expected_output_size)
            built = valid_built_image(built_candidate, expected_output_size)
            fitc_result = retry_result
        if not built:
            continue
        fitc = candidate_fitc
        published_output = publish_clearme_output(built, output_source, prep.out_root)
        if args.dual_split:
            split_outputs = split_cleared_output(args, published_output, prep)
            published_output = None
        break
    return BuildResult(published_output, split_outputs, fitc_runs, fitc)


def remove_temp_input(prep: PrepareInput) -> None:
    if prep.temp_merged_input and prep.temp_merged_input.exists():
        prep.temp_merged_input.unlink()
    if prep.temp_input_dir and prep.temp_input_dir.exists():
        shutil.rmtree(prep.temp_input_dir)


def command_prepare(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    repo_value = pick_arg(args, config, "repo", "csme_repo")
    fitc_value = pick_arg(args, config, "fitc_root")
    out_value = pick_arg(args, config, "out")
    mea_value = pick_arg(args, config, "mea")
    require_config_values(csme_repo=repo_value, fitc_root=fitc_value)
    repo = Path(repo_value).resolve()
    fitc_root = Path(fitc_value).resolve()
    prep = prepare_input(args, out_value)
    mea = resolve_mea(mea_value, repo, fitc_root)
    info = prepare_firmware_info(args, prep.image, mea)
    if info.major is None or info.major < 11 or info.major > 20:
        raise RuntimeError(f"Input CSME major version must be 11-20, got: {info.version or 'unknown'}")

    print(f"[2/5] Detected CSME {info.version}, SKU: {display_sku(info.sku) or 'unknown'}", flush=True)
    rgn, ranked = matching_rgn(args, repo, info)
    print(f"[4/5] Selected RGN: {log_path_name(rgn)}", flush=True)
    fitc_candidates = ranked_fit_candidates(args, fitc_root, info)
    fitc = fitc_candidates[0] if fitc_candidates else None
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    work_source = prep.source_image or prep.image
    workdir = prep.out_root / f"{work_source.stem}_clearme_{stamp}"
    workdir.mkdir(parents=True, exist_ok=False)
    input_copy, rgn_copy = copy_inputs(workdir, prep.image, rgn)

    manifest = {
        "input": str(prep.image),
        "workdir": str(workdir),
        "input_copy": str(input_copy),
        "rgn_copy": str(rgn_copy),
        "selected_rgn": str(rgn),
        "fitc": str(fitc) if fitc else "",
        "mea": str(mea),
        "detected": asdict(info),
        "rgn_candidates": ranked,
    }
    (workdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_manual_steps(workdir, fitc)

    result = try_fit_build(args, prep, workdir, input_copy, rgn_copy, fitc_candidates)
    if result.published_output or result.split_outputs:
        manifest["fitc"] = str(result.fitc)
        manifest["published_output"] = str(result.published_output) if result.published_output else ""
        manifest["split_outputs"] = result.split_outputs
        (workdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        cleanup_job(workdir)
        remove_temp_input(prep)

    status = "cleared" if result.published_output or result.split_outputs else "prepared"
    failure_reason = "" if status == "cleared" else summarize_fitc_failure(result.fitc_runs)
    if status != "cleared":
        cleanup_job(workdir)
        remove_temp_input(prep)
    print(json.dumps({
        "status": status,
        "workdir": str(workdir),
        "selected_rgn": str(rgn),
        "published_output": str(result.published_output) if result.published_output else "",
        "split_outputs": result.split_outputs,
        "failure_reason": failure_reason,
        "next_step": "" if status == "cleared" else "Try another ME Region/FIT selection or inspect the error details above.",
    }, indent=2))
    return 0


def command_verify(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    mea_value = pick_arg(args, config, "mea")
    configured_mea = Path(mea_value).resolve() if mea_value else None
    mea = configured_mea if configured_mea and configured_mea.exists() else find_me_analyzer([Path.cwd()])
    if not mea:
        raise ValueError("ME Analyzer not found. Keep MEA/MEA.py in the project, edit config.json, or pass --mea.")
    before = analyze_with_mea(mea, Path(args.input).resolve())
    after = analyze_with_mea(mea, Path(args.output).resolve())
    ok = before.major == after.major and before.minor == after.minor
    before_sku = normalize_sku(before.sku)
    after_sku = normalize_sku(after.sku)
    if before_sku and after_sku:
        ok = ok and (before_sku in after_sku or after_sku in before_sku)
    print(json.dumps({"ok": ok, "before": asdict(before), "after": asdict(after)}, indent=2))
    return 0 if ok else 2


def command_analyze(args: argparse.Namespace) -> int:
    image = None
    temp_dir = None
    try:
        if args.dual_file1 and args.dual_file2:
            file1 = Path(args.dual_file1).resolve()
            file2 = Path(args.dual_file2).resolve()
            temp_dir = tempfile.TemporaryDirectory(prefix="AutoClearME_")
            image, chip1_size = merge_dual_inputs(file1, file2, Path(temp_dir.name))
            print(f"Merged Dual BIOS for analysis: {log_path_name(file1)} + {log_path_name(file2)}", flush=True)
        else:
            if not args.input:
                raise ValueError("Missing --input, or use --dual-file1 and --dual-file2 for Dual BIOS analysis.")
            image = Path(args.input).resolve()
            chip1_size = None
            data, excess = trim_bios_data(image.read_bytes())
            if excess > 0:
                temp_dir = tempfile.TemporaryDirectory(prefix="AutoClearME_")
                trimmed_image = Path(temp_dir.name) / image.name
                trimmed_image.write_bytes(data)
                print(f"[INFO] Cleared {excess} bytes trailing metadata from {image.name} (new size: {len(data)} bytes).", flush=True)
                image = trimmed_image

        config = load_config(args.config)
        mea_value = pick_arg(args, config, "mea")
        configured_mea = Path(mea_value).resolve() if mea_value else None
        mea = configured_mea if configured_mea and configured_mea.exists() else find_me_analyzer([Path.cwd()])
        if not mea:
            raise ValueError("ME Analyzer not found. Keep MEA/MEA.py in the project, edit config.json, or pass --mea.")

        print(f"[ANALYZE] Input: {log_path_name(image)}", flush=True)
        print(f"[ANALYZE] ME Analyzer: {log_path_name(mea)}", flush=True)
        info = analyze_with_mea(mea, image)
        if not info.version or info.major is None:
            raise RuntimeError(
                "ME Analyzer could not detect CSME information in this file. "
                "Check that the selected file is a full BIOS/SPI dump or ME region."
            )
        if info.major < 11 or info.major > 20:
            raise RuntimeError(f"Input CSME major version must be 11-20, got: {info.version}")
        info.bios_vendor, info.bios_version = detect_bios_version(image.read_bytes())
        print(f"[ANALYZE] Version: {info.version or 'unknown'}", flush=True)
        print(f"[ANALYZE] BIOS Version: {info.bios_version or 'not detected'}", flush=True)
        print(f"[ANALYZE] SKU: {display_sku(info.sku) or 'unknown'}", flush=True)
        type_label = info.type or "unknown"
        if info.data_state:
            type_label = f"{type_label}, {info.data_state}"
        print(f"[ANALYZE] Type: {type_label}", flush=True)
        detected = asdict(info)
        detected.pop("raw", None)
        detected["sku"] = display_sku(info.sku)
        rgn_candidates = []
        repo_value = pick_arg(args, config, "repo", "csme_repo")
        if repo_value:
            try:
                _best_rgn, ranked = find_best_rgn(Path(repo_value).resolve(), info)
                rgn_candidates = [
                    {"path": item["path"], "label": candidate_label(Path(item["path"])), "score": item["score"]}
                    for item in ranked
                ]
            except Exception as exc:
                rgn_candidates = [{"path": "", "label": f"No matching ME Region found: {exc}", "score": 0}]
        fitc_candidates = []
        fitc_value = pick_arg(args, config, "fitc_root")
        if fitc_value:
            fitc_candidates = [
                {
                    "path": item["path"],
                    "label": fitc_label(Path(item["path"])),
                    "score": item["score"],
                }
                for item in find_ranked_fitc_candidates(Path(fitc_value).resolve(), info)
            ]
        print(json.dumps({
            "status": "analyzed",
            "input": str(image),
            "chip1_size": chip1_size,
            "mea": str(mea),
            "detected": detected,
            "rgn_candidates": rgn_candidates,
            "fitc_candidates": fitc_candidates,
        }, indent=2))
        return 0
    finally:
        if temp_dir:
            temp_dir.cleanup()


def format_winkey_candidate(candidate: WinKeyCandidate) -> str:
    classification = re.sub(r"^likely\s+", "", candidate.classification, flags=re.IGNORECASE)
    return (
        f"  Offset: [0x{candidate.offset:X}, 0x{candidate.offset + candidate.length:X}]\n"
        f"  {candidate.key} | {classification}"
    )


def command_winkey(args: argparse.Namespace) -> int:
    failed = 0
    for value in args.input:
        path = Path(value).resolve()
        print(f"[INFO] Finding Win Key in {log_path_name(path)}", flush=True)
        try:
            if not path.exists():
                print(f"File does not exist: {log_path_name(path)}", flush=True)
                failed += 1
                continue
            candidates = find_winkeys(path.read_bytes())
            if not candidates:
                print("  No plaintext Windows product key candidate found", flush=True)
                continue
            unique_candidates = {}
            for candidate in candidates:
                classification = re.sub(r"^likely\s+", "", candidate.classification, flags=re.IGNORECASE)
                unique_candidates.setdefault((candidate.key, classification), candidate)
            for candidate in unique_candidates.values():
                print(format_winkey_candidate(candidate), flush=True)
        except Exception as exc:
            failed += 1
            print(f"  Find WinKey failed: {exc}", flush=True)
    return 0 if failed == 0 else 2


def command_lenovo_dmi(args: argparse.Namespace) -> int:
    failed = 0
    for value in args.input:
        path = Path(value).resolve()
        print(f"[INFO] Finding Lenovo DMI in {log_path_name(path)}", flush=True)
        try:
            if not path.exists():
                print(f"  File does not exist: {log_path_name(path)}", flush=True)
                failed += 1
                continue
            groups = find_lenovo_dmi_groups(path.read_bytes())
            if not groups:
                print("  No Lenovo DMI found", flush=True)
                continue
            for start, end, items in groups:
                print(f"  Offset: [0x{start:X}, 0x{end:X}]", flush=True)
                for item in items:
                    print(f"  {item.label}: {item.value}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  Find Lenovo DMI failed: {exc}", flush=True)
    return 0 if failed == 0 else 2


def command_asus_dmi(args: argparse.Namespace) -> int:
    failed = 0
    for value in args.input:
        path = Path(value).resolve()
        print(f"[INFO] Finding Asus DMI in {log_path_name(path)}...", flush=True)
        try:
            if not path.exists():
                print(f"  File does not exist: {log_path_name(path)}", flush=True)
                failed += 1
                continue
            groups = find_asus_dmi_groups(path.read_bytes())
            if not groups:
                print("  No Asus DMI found", flush=True)
                continue
            for start, end, items in groups:
                print(f"  Offset: [0x{start:X}, 0x{end:X}]", flush=True)
                for item in items:
                    print(f"  {item.label}: {item.value}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  Find Asus DMI failed: {exc}", flush=True)
    return 0 if failed == 0 else 2


def command_asus_dmi_export(args: argparse.Namespace) -> int:
    path = Path(args.input).resolve()
    print(f"[INFO] Export Asus DMI from {log_path_name(path)}...", flush=True)
    if not path.exists():
        raise RuntimeError(f"File does not exist: {log_path_name(path)}")
    output, count = export_asus_dmi(path)
    print(f"  Blocks: {count}", flush=True)
    print(f"  Output: {log_path_name(output)}", flush=True)
    return 0


def command_lenovo_dmi_export(args: argparse.Namespace) -> int:
    path = Path(args.input).resolve()
    print(f"[INFO] Export Lenovo DMI from {log_path_name(path)}", flush=True)
    if not path.exists():
        raise RuntimeError(f"File does not exist: {log_path_name(path)}")
    output, count = export_lenovo_dmi(path)
    print(f"  Output: {log_path_name(output)}", flush=True)
    return 0


def command_hp_dmi(args: argparse.Namespace) -> int:
    failed = 0
    for value in args.input:
        path = Path(value).resolve()
        print(f"[INFO] Finding HP DMI in {log_path_name(path)}", flush=True)
        try:
            if not path.exists():
                print(f"  File does not exist: {log_path_name(path)}", flush=True)
                failed += 1
                continue
            groups = find_hp_dmi_groups(path.read_bytes())
            if not groups:
                print("  No HP DMI found", flush=True)
                continue
            for start, end, items in groups:
                print(f"  Offset: [0x{start:X}, 0x{end:X}]", flush=True)
                for item in items:
                    print(f"  {item.label}: {item.value}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  Find HP DMI failed: {exc}", flush=True)
    return 0 if failed == 0 else 2


def command_hp_dmi_export(args: argparse.Namespace) -> int:
    path = Path(args.input).resolve()
    print(f"[INFO] Export HP DMI from {log_path_name(path)}", flush=True)
    if not path.exists():
        raise RuntimeError(f"File does not exist: {log_path_name(path)}")
    output, count = export_hp_dmi(path)
    print(f"  Output: {log_path_name(output)}", flush=True)
    return 0


def command_acer_dmi(args: argparse.Namespace) -> int:
    failed = 0
    for value in args.input:
        path = Path(value).resolve()
        print(f"[INFO] Finding Acer DMI in {log_path_name(path)}", flush=True)
        try:
            if not path.exists():
                print(f"  File does not exist: {log_path_name(path)}", flush=True)
                failed += 1
                continue
            groups = find_acer_dmi_groups(path.read_bytes())
            if not groups:
                print("  No Acer DMI found", flush=True)
                continue
            for start, end, items in groups:
                print(f"  Offset: [0x{start:X}, 0x{end:X}]", flush=True)
                for item in items:
                    print(f"  {item.label}: {item.value}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  Find Acer DMI failed: {exc}", flush=True)
    return 0 if failed == 0 else 2


def command_acer_dmi_export(args: argparse.Namespace) -> int:
    path = Path(args.input).resolve()
    print(f"[INFO] Export Acer DMI from {log_path_name(path)}", flush=True)
    if not path.exists():
        raise RuntimeError(f"File does not exist: {log_path_name(path)}")
    output, count = export_acer_dmi(path)
    print(f"  Output: {log_path_name(output)}", flush=True)
    return 0


def command_dell_dmi(args: argparse.Namespace) -> int:
    failed = 0
    for value in args.input:
        path = Path(value).resolve()
        print(f"[INFO] Finding Dell DMI in {log_path_name(path)}", flush=True)
        try:
            if not path.exists():
                print(f"  File does not exist: {log_path_name(path)}", flush=True)
                failed += 1
                continue
            groups = find_dell_dmi_groups(path.read_bytes())
            if not groups:
                print("  No Dell DMI found", flush=True)
                continue
            for start, end, items in groups:
                print(f"  Offset: [0x{start:X}, 0x{end:X}]", flush=True)
                for item in items:
                    print(f"  {item.label}: {item.value}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  Find Dell DMI failed: {exc}", flush=True)
    return 0 if failed == 0 else 2


def command_dell_dmi_export(args: argparse.Namespace) -> int:
    path = Path(args.input).resolve()
    print(f"[INFO] Export Dell DMI from {log_path_name(path)}", flush=True)
    try:
        if not path.exists():
            print(f"  File does not exist: {log_path_name(path)}", flush=True)
            return 2
        output, count = export_dell_dmi(path)
        print(f"  Output: {log_path_name(output)}", flush=True)
        return 0
    except Exception as exc:
        print(f"  Export Dell DMI failed: {exc}", flush=True)
        return 2


def command_dell_pfs_extract(args: argparse.Namespace) -> int:
    path = Path(args.input).resolve()
    output = unique_output_path(path.with_name("DELL_PFS"))
    print(f"[INFO] Extract Dell PFS from {log_path_name(path)}", flush=True)
    try:
        if not path.exists():
            print(f"  File does not exist: {log_path_name(path)}", flush=True)
            return 2
        try:
            from biosutilities.dell_pfs_extract import DellPfsExtract
        except Exception as exc:
            print(f"  Missing dependency: biosutilities ({exc})", flush=True)
            print("  Please rebuild with Build.bat or use the full portable release package.", flush=True)
            return 2
        extractor = DellPfsExtract(
            input_object=str(path),
            extract_path=str(output),
            advanced=args.advanced,
            structure=args.structure,
        )
        if not extractor.check_format():
            print("  Dell PFS/PKG/TXT/RCV format was not detected.", flush=True)
            return 2
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            extractor.parse_format()
        for line in capture.getvalue().splitlines():
            line = line.rstrip()
            if line.strip():
                print(line, flush=True)
        print(f"  Output: {log_path_name(output)}", flush=True)
        return 0
    except Exception as exc:
        print(f"  Extract Dell PFS failed: {exc}", flush=True)
        return 2


def command_lenovo_dmi_import(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()
    package = Path(args.dmi).resolve()
    print(f"[INFO] Import DMI into {log_path_name(target)}", flush=True)
    if not target.exists():
        raise RuntimeError(f"Target BIOS does not exist: {log_path_name(target)}")
    if not package.exists():
        raise RuntimeError(f"DMI package does not exist: {log_path_name(package)}")
    output, count, kind = import_dmi_package(target, package)
    print(f"  Type: {kind}", flush=True)
    print(f"  Blocks: {count}", flush=True)
    print(f"  Output: {log_path_name(output)}", flush=True)
    return 0


def command_unlock_asus(args: argparse.Namespace) -> int:
    failed = 0
    for value in args.input:
        path = Path(value).resolve()
        print(f"[INFO] Unlock ASUS in {log_path_name(path)}", flush=True)
        try:
            if not path.exists():
                print(f"  File does not exist: {log_path_name(path)}", flush=True)
                failed += 1
                continue
            output, _cleared_ranges = unlock_asus_password(path, args.length)
            if output:
                print(f"  Output: {log_path_name(output)}", flush=True)
            else:
                print("  No password found", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  Unlock ASUS failed: {exc}", flush=True)
    return 0 if failed == 0 else 2


def command_unlock_acer(args: argparse.Namespace) -> int:
    failed = 0
    for value in args.input:
        path = Path(value).resolve()
        print(f"[INFO] Unlock ACER in {log_path_name(path)}", flush=True)
        try:
            if not path.exists():
                print(f"  File does not exist: {log_path_name(path)}", flush=True)
                failed += 1
                continue
            output, _cleared_ranges = unlock_acer_password(path)
            if output:
                print(f"  Output: {log_path_name(output)}", flush=True)
            else:
                print("  No password found", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  Unlock ACER failed: {exc}", flush=True)
    return 0 if failed == 0 else 2


def command_unlock_hp(args: argparse.Namespace) -> int:
    failed = 0
    for value in args.input:
        path = Path(value).resolve()
        print(f"[INFO] Unlock HP in {log_path_name(path)}", flush=True)
        try:
            if not path.exists():
                print(f"  File does not exist: {log_path_name(path)}", flush=True)
                failed += 1
                continue
            output, _cleared_ranges = unlock_hp_password(path)
            if output:
                print(f"  Output: {log_path_name(output)}", flush=True)
            else:
                print("  No password found", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  Unlock HP failed: {exc}", flush=True)
    return 0 if failed == 0 else 2


def command_unlock_dell_8fc8(args: argparse.Namespace) -> int:
    failed = 0
    for value in args.input:
        path = Path(value).resolve()
        print(f"[INFO] Unlock Dell 8FC8 in {log_path_name(path)}", flush=True)
        try:
            if not path.exists():
                print(f"  File does not exist: {log_path_name(path)}", flush=True)
                failed += 1
                continue
            output, cleared_ranges = unlock_dell_8fc8_password(path)
            if output:
                for offset, length in cleared_ranges:
                    print(f"  Patch: [0x{offset:X}, 0x{offset + length:X}]", flush=True)
                print(f"  Output: {log_path_name(output)}", flush=True)
            else:
                print("  Already unlocked", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  Unlock Dell 8FC8 failed: {exc}", flush=True)
    return 0 if failed == 0 else 2


def command_merge_bios(args: argparse.Namespace) -> int:
    file1 = Path(args.file1).resolve()
    file2 = Path(args.file2).resolve()
    print(f"[INFO] Merging BIOS: {log_path_name(file1)} + {log_path_name(file2)}", flush=True)
    try:
        if not file1.exists():
            raise FileNotFoundError(f"BIOS 1 does not exist: {log_path_name(file1)}")
        if not file2.exists():
            raise FileNotFoundError(f"BIOS 2 does not exist: {log_path_name(file2)}")
        out_root = Path(args.out).resolve() if args.out else file1.parent
        output, inputs = merge_bios_files(file1, file2, out_root)
        for source, size, excess in inputs:
            if excess:
                print(f"  Trim: {log_path_name(source)} removed {excess} bytes, size={size}", flush=True)
            else:
                print(f"  Size: {log_path_name(source)} = {size}", flush=True)
        print(f"  Output: {log_path_name(output)}", flush=True)
        return 0
    except Exception as exc:
        print(f"  Merge BIOS failed: {exc}", flush=True)
        return 2


def command_split_bios(args: argparse.Namespace) -> int:
    source = Path(args.input).resolve()
    print(f"[INFO] Splitting BIOS: {log_path_name(source)}", flush=True)
    try:
        if not source.exists():
            raise FileNotFoundError(f"BIOS file does not exist: {log_path_name(source)}")
        bios1_size = parse_bios_mb_size(args.bios1_size)
        bios2_size = parse_bios_mb_size(args.bios2_size)
        out_root = Path(args.out).resolve() if args.out else source.parent
        chip1, chip2, excess = split_bios_file(source, bios1_size, bios2_size, out_root)
        if excess:
            print(f"  Trim: {log_path_name(source)} removed {excess} bytes", flush=True)
        print(f"  BIOS 1: {bios1_size} bytes -> {log_path_name(chip1)}", flush=True)
        print(f"  BIOS 2: {bios2_size} bytes -> {log_path_name(chip2)}", flush=True)
        print(f"  Output: {log_path_name(chip1)}", flush=True)
        print(f"  Output: {log_path_name(chip2)}", flush=True)
        return 0
    except Exception as exc:
        print(f"  Split BIOS failed: {exc}", flush=True)
        return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Automate Intel CSME 11-20 clean ME preparation.")
    sub = p.add_subparsers(dest="cmd", required=True)

    prep = sub.add_parser("prepare", help="Analyze input, select matching RGN, create FIT workspace.")
    prep.add_argument("--input", help="Dumped full SPI/BIOS image or ME region.")
    prep.add_argument("--config", help="Path to config.json. Defaults to config.json next to this script.")
    prep.add_argument("--repo", help="ME Region root folder. Overrides config csme_repo.")
    prep.add_argument("--fit-root", dest="fitc_root", metavar="FIT_ROOT", help="Root folder containing FIT tools 11-20. Overrides config fit_root.")
    prep.add_argument("--fitc-root", dest="fitc_root", help=argparse.SUPPRESS)
    prep.add_argument("--mea", help="Path to MEA.py, MEA.exe, or ME Analyzer.exe.")
    prep.add_argument("--out", help="Output jobs folder. Overrides config out.")
    prep.add_argument("--try-fit", dest="try_fitc", action="store_true", help="Try FIT command-line build if supported.")
    prep.add_argument("--try-fitc", dest="try_fitc", action="store_true", help=argparse.SUPPRESS)
    prep.add_argument("--rgn", help=argparse.SUPPRESS)
    prep.add_argument("--fitc", help=argparse.SUPPRESS)
    prep.add_argument("--detected-version", help=argparse.SUPPRESS)
    prep.add_argument("--detected-sku", help=argparse.SUPPRESS)
    prep.add_argument("--detected-type", help=argparse.SUPPRESS)
    prep.add_argument("--detected-data-state", help=argparse.SUPPRESS)
    prep.add_argument("--dual-split", action="store_true", help="Split cleared merged image into CHIP1/CHIP2 outputs.")
    prep.add_argument("--chip1-size", help="First chip size for --dual-split, for example 8MB or 16MB.")
    prep.add_argument("--dual-file1", help="Dual BIOS file 1. It will be placed first in merged image.")
    prep.add_argument("--dual-file2", help="Dual BIOS file 2. It will be placed second in merged image.")
    prep.set_defaults(func=command_prepare)

    ver = sub.add_parser("verify", help="Compare MEA version/SKU before and after.")
    ver.add_argument("--config", help="Path to config.json. Defaults to config.json next to this script.")
    ver.add_argument("--input", required=True)
    ver.add_argument("--output", required=True)
    ver.add_argument("--mea", help="Path to MEA.py, MEA.exe, or ME Analyzer.exe. Overrides config mea.")
    ver.set_defaults(func=command_verify)

    ana = sub.add_parser("analyze", help="Analyze input with ME Analyzer.")
    ana.add_argument("--config", help="Path to config.json. Defaults to config.json next to this script.")
    ana.add_argument("--input", help="Dumped full SPI/BIOS image or ME region.")
    ana.add_argument("--dual-file1", help="Dual BIOS file 1. It will be placed first in merged image.")
    ana.add_argument("--dual-file2", help="Dual BIOS file 2. It will be placed second in merged image.")
    ana.add_argument("--mea", help="Path to MEA.py, MEA.exe, or ME Analyzer.exe. Overrides config mea.")
    ana.set_defaults(func=command_analyze)

    winkey = sub.add_parser("winkey", help="Find plaintext Windows product key candidates in BIOS dump(s).")
    winkey.add_argument("--input", action="append", required=True, help="BIOS dump to scan. Repeat for Dual BIOS.")
    winkey.set_defaults(func=command_winkey)

    merge_bios = sub.add_parser("merge-bios", help="Trim and merge two BIOS files in selected order.")
    merge_bios.add_argument("--file1", required=True, help="BIOS 1. It will be placed first.")
    merge_bios.add_argument("--file2", required=True, help="BIOS 2. It will be placed second.")
    merge_bios.add_argument("--out", help="Output folder. Defaults to BIOS 1 folder.")
    merge_bios.set_defaults(func=command_merge_bios)

    split_bios = sub.add_parser("split-bios", help="Split a merged BIOS file by BIOS 1 and BIOS 2 sizes.")
    split_bios.add_argument("--input", required=True, help="Merged BIOS file.")
    split_bios.add_argument("--bios1-size", required=True, help="BIOS 1 size, for example 8MB, 16MB, or 8.")
    split_bios.add_argument("--bios2-size", required=True, help="BIOS 2 size, for example 8MB, 16MB, or 8.")
    split_bios.add_argument("--out", help="Output folder. Defaults to input folder.")
    split_bios.set_defaults(func=command_split_bios)

    lenovo_dmi = sub.add_parser("lenovo-dmi", help="Find Lenovo DMI data in BIOS dump(s).")
    lenovo_dmi.add_argument("--input", action="append", required=True, help="BIOS dump to scan. Repeat for Dual BIOS.")
    lenovo_dmi.set_defaults(func=command_lenovo_dmi)

    asus_dmi = sub.add_parser("asus-dmi", help="Find ASUS manufacturing DMI data in BIOS dump(s).")
    asus_dmi.add_argument("--input", action="append", required=True, help="BIOS dump to scan. Repeat for Dual BIOS.")
    asus_dmi.set_defaults(func=command_asus_dmi)

    asus_export = sub.add_parser("asus-dmi-export", help="Export Asus DMI blocks to a .asusdmi package.")
    asus_export.add_argument("--input", required=True, help="Source BIOS dump.")
    asus_export.set_defaults(func=command_asus_dmi_export)

    lenovo_export = sub.add_parser("lenovo-dmi-export", help="Export Lenovo DMI blocks to a .lendmi package.")
    lenovo_export.add_argument("--input", required=True, help="Source BIOS dump.")
    lenovo_export.set_defaults(func=command_lenovo_dmi_export)

    hp_dmi = sub.add_parser("hp-dmi", help="Find HP DMI data in BIOS dump(s).")
    hp_dmi.add_argument("--input", action="append", required=True, help="BIOS dump to scan. Repeat for Dual BIOS.")
    hp_dmi.set_defaults(func=command_hp_dmi)

    hp_export = sub.add_parser("hp-dmi-export", help="Export HP DMI blocks to a .hpdmi package.")
    hp_export.add_argument("--input", required=True, help="Source BIOS dump.")
    hp_export.set_defaults(func=command_hp_dmi_export)

    acer_dmi = sub.add_parser("acer-dmi", help="Find Acer DMI data in BIOS dump(s).")
    acer_dmi.add_argument("--input", action="append", required=True, help="BIOS dump to scan. Repeat for Dual BIOS.")
    acer_dmi.set_defaults(func=command_acer_dmi)

    acer_export = sub.add_parser("acer-dmi-export", help="Export Acer DMI blocks to a .acerdmi package.")
    acer_export.add_argument("--input", required=True, help="Source BIOS dump.")
    acer_export.set_defaults(func=command_acer_dmi_export)

    dell_dmi = sub.add_parser("dell-dmi", help="Find Dell DMI data in BIOS dump(s).")
    dell_dmi.add_argument("--input", action="append", required=True, help="BIOS dump to scan. Repeat for Dual BIOS.")
    dell_dmi.set_defaults(func=command_dell_dmi)

    dell_export = sub.add_parser("dell-dmi-export", help="Export Dell DMI blocks to a .delldmi package.")
    dell_export.add_argument("--input", required=True, help="Source BIOS dump.")
    dell_export.set_defaults(func=command_dell_dmi_export)

    dell_pfs = sub.add_parser("dell-pfs-extract", help="Extract Dell PFS/PKG/TXT/RCV update images.")
    dell_pfs.add_argument("--input", required=True, help="Dell BIOS update/PFS/PKG/TXT/RCV image. Output goes to DELL_PFS next to input.")
    dell_pfs.add_argument("--advanced", action="store_true", help="Enable BIOSUtilities advanced extraction.")
    dell_pfs.add_argument("--structure", action="store_true", help="Preserve BIOSUtilities structure output.")
    dell_pfs.set_defaults(func=command_dell_pfs_extract)

    lenovo_import = sub.add_parser("lenovo-dmi-import", help="Import Lenovo DMI blocks into another BIOS dump.")
    lenovo_import.add_argument("--dmi", required=True, help="Lenovo DMI .lendmi package.")
    lenovo_import.add_argument("--target", required=True, help="Target BIOS dump.")
    lenovo_import.set_defaults(func=command_lenovo_dmi_import)

    unlock = sub.add_parser("unlock-asus", help="Clear ASUS BIOS password.")
    unlock.add_argument("--input", action="append", required=True, help="BIOS dump to patch. Repeat for Dual BIOS.")
    unlock.add_argument("--length", type=int, default=ASUS_UNLOCK_ZERO_LENGTH, help=argparse.SUPPRESS)
    unlock.set_defaults(func=command_unlock_asus)

    unlock_acer = sub.add_parser("unlock-acer", help="Clear ACER BIOS password.")
    unlock_acer.add_argument("--input", action="append", required=True, help="BIOS dump to patch. Repeat for Dual BIOS.")
    unlock_acer.set_defaults(func=command_unlock_acer)

    unlock_hp = sub.add_parser("unlock-hp", help="Clear HP BIOS password.")
    unlock_hp.add_argument("--input", action="append", required=True, help="BIOS dump to patch. Repeat for Dual BIOS.")
    unlock_hp.set_defaults(func=command_unlock_hp)

    unlock_dell_8fc8 = sub.add_parser("unlock-dell-8fc8", help="Clear Dell 8FC8 BIOS lock.")
    unlock_dell_8fc8.add_argument("--input", action="append", required=True, help="Dell 8FC8 BIOS dump to patch.")
    unlock_dell_8fc8.set_defaults(func=command_unlock_dell_8fc8)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
