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
import datetime as _dt
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
    raw: str = ""


@dataclass
class PrepareInput:
    image: Path
    out_root: Path
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
    m = RGN_RE.search(path.name)
    info = FirmwareInfo()
    if not m:
        vm = VERSION_RE.search(path.name)
        if vm:
            info.version = vm.group(0)
            info.major = int(vm.group("major"))
            info.minor = int(vm.group("minor"))
        return info
    info.version = m.group("version")
    info.sku = sku_from_filename(path.name) or normalize_sku(m.group("sku"))
    vm = VERSION_RE.search(info.version)
    if vm:
        info.major = int(vm.group("major"))
        info.minor = int(vm.group("minor"))
    info.type = "Region"
    return info


def normalize_sku(value: str) -> str:
    text = re.sub(r"[^a-z0-9.]+", " ", value.lower()).strip()
    text = re.sub(r"\bcon\b", "consumer", text)
    text = re.sub(r"\bcor\b", "corporate", text)
    text = re.sub(r"\bslm\b", "slim", text)
    text = re.sub(r"\bnopdm\b|\bnpdm\b", "npdm", text)
    aliases = {
        "consumer h": "consumer h",
        "consumer lp": "consumer lp",
        "consumer h d": "consumer h",
        "consumer lp c": "consumer lp",
        "corporate h": "corporate h",
        "corporate lp": "corporate lp",
        "corporate h d": "corporate h",
        "corporate lp c": "corporate lp",
        "consumer lp": "consumer lp",
        "consumer n": "consumer n",
        "corporate h": "corporate h",
        "corporate lp": "corporate lp",
        "corporate n": "corporate n",
        "slim h": "slim h",
        "slim lp": "slim lp",
        "slim n": "slim n",
        "1.5mb": "1.5mb",
        "5mb": "5mb",
        "slim": "slim",
        "h": "h",
        "lp": "lp",
        "n": "n",
    }
    for key, normalized in aliases.items():
        if key in text:
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
    upper = name.upper()
    if "_CON_LP" in upper:
        return "consumer lp"
    if "_CON_H" in upper:
        return "consumer h"
    if "_CON_N" in upper:
        return "consumer n"
    if "_COR_LP" in upper:
        return "corporate lp"
    if "_COR_H" in upper:
        return "corporate h"
    if "_COR_N" in upper:
        return "corporate n"
    if "_SLM_LP" in upper:
        return "slim lp"
    if "_SLM_H" in upper:
        return "slim h"
    if "_SLM_N" in upper:
        return "slim n"
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
    cand_sku = normalize_sku(c.sku or candidate.name)
    if input_sku:
        if not sku_matches(input_sku, cand_sku):
            return 0, "sku-mismatch"
        score += 40
        reasons.append("sku")
    else:
        return 0, "missing-input-sku"
    name_lower = candidate.name.lower()
    if "prd" in name_lower:
        score += 5
    if "rgn" in name_lower:
        score += 10
        reasons.append("rgn")
    cv = version_tuple(c.version)
    iv = version_tuple(input_info.version)
    if cv == iv:
        score += 120
        reasons.append("exact-version")
        return score, ",".join(reasons)
    distance = abs(version_rank(c.version) - version_rank(input_info.version))
    score += max(0, 80 - distance / 10_000)
    reasons.append("nearest-version")
    return score, ",".join(reasons)


def find_best_rgn(repo: Path, input_info: FirmwareInfo) -> tuple[Path, list[dict]]:
    candidates = [
        p for p in repo.rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".bin", ".rgn"}
        and "prd" in p.name.lower()
        and ("rgn" in p.name.lower() or "extr" in p.name.lower())
    ]
    ranked = []
    for p in candidates:
        score, reason = score_rgn(input_info, p)
        if score:
            ranked.append({"path": str(p), "score": score, "reason": reason})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    if not ranked:
        raise FileNotFoundError("No matching PRD RGN firmware found in ME Region root.")
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
    score = 0
    reasons = []
    if input_info.major is not None:
        if major != input_info.major:
            return 0, "major-mismatch"
        score += 150
        reasons.append("major")
    if input_info.minor is not None and minor == input_info.minor:
        score += 80
        reasons.append("minor")
    distance = abs(version_rank(str(candidate)) - version_rank(input_info.version))
    score += max(0, 100 - distance / 10_000)
    reasons.append("nearest-version")
    return score, ",".join(reasons)


def find_ranked_fitc_candidates(fitc_root: Path, input_info: FirmwareInfo) -> list[dict]:
    ranked = []
    candidates = find_fitc_candidates(fitc_root, input_info.major)
    if input_info.minor is not None:
        same_minor = [path for path in candidates if version_tuple(str(path))[1] == input_info.minor]
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


def maybe_run_fitc(fitc: Path, workdir: Path, input_image: Path, me_region: Path, output_image: Path) -> dict:
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

    build_cmd = [
        str(fitc),
        "-b",
        "-f",
        str(config_xml),
        "-me",
        str(me_region),
        "-o",
        str(output_image),
    ]
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
    me_region: Path,
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

    patch_modular_config_me_region(config_xml, clean_config_xml, me_region)
    build_cmd = [str(fitc), "--loadconfig", str(clean_config_xml), "--build", str(output_image)]
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
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    merged = out_root / f"MERGED_{stamp}.bin"
    with merged.open("wb") as out:
        with file1.open("rb") as fh:
            shutil.copyfileobj(fh, out)
        with file2.open("rb") as fh:
            shutil.copyfileobj(fh, out)
    return merged, file1.stat().st_size


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


def prepare_input(args: argparse.Namespace, out_value: str | None) -> PrepareInput:
    if args.dual_file1 and args.dual_file2:
        file1 = Path(args.dual_file1).resolve()
        file2 = Path(args.dual_file2).resolve()
        out_root = Path(out_value).resolve() if out_value else file1.parent
        out_root.mkdir(parents=True, exist_ok=True)
        temp_merged_input, merged_chip1_size = merge_dual_inputs(file1, file2, out_root)
        print(f"[0/5] Merged Dual BIOS files in user order: {file1} + {file2}", flush=True)
        return PrepareInput(
            image=temp_merged_input,
            out_root=out_root,
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
    return PrepareInput(image=image, out_root=out_root)


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
        print(f"[1/5] Using cached ME Analyzer result: {image}", flush=True)
        return info
    print(f"[1/5] Analyzing input with ME Analyzer: {image}", flush=True)
    if not mea:
        raise FileNotFoundError("ME Analyzer not found. Pass --mea path/to/MEA.py or MEA.exe.")
    return analyze_with_mea(mea, image)


def matching_rgn(args: argparse.Namespace, repo: Path, info: FirmwareInfo) -> tuple[Path, list[dict]]:
    print(f"[3/5] Searching matching PRD_RGN in: {repo}", flush=True)
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
        print(f"[5/5] Trying FIT CLI build ({idx}/{len(fitc_candidates)}): {fitc_label(candidate_fitc)}", flush=True)
        work_output = clearme_output_name(prep.image, workdir)
        fitc_result = maybe_run_fitc(candidate_fitc, workdir, input_copy, rgn_copy, work_output)
        fitc_runs.append(fitc_result)
        (workdir / "fitc_run.json").write_text(json.dumps(fitc_runs, indent=2), encoding="utf-8")
        built = work_output if work_output.exists() else find_built_image(workdir, candidate_fitc)
        if not (fitc_succeeded(fitc_result, work_output) or built):
            continue
        fitc = candidate_fitc
        published_output = publish_clearme_output(built, prep.image, prep.out_root)
        if args.dual_split:
            split_outputs = split_cleared_output(args, published_output, prep)
            published_output = None
        break
    return BuildResult(published_output, split_outputs, fitc_runs, fitc)


def remove_temp_input(prep: PrepareInput) -> None:
    if prep.temp_merged_input and prep.temp_merged_input.exists():
        prep.temp_merged_input.unlink()


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
    print(f"[4/5] Selected RGN: {rgn}", flush=True)
    fitc_candidates = ranked_fit_candidates(args, fitc_root, info)
    fitc = fitc_candidates[0] if fitc_candidates else None
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    workdir = prep.out_root / f"{prep.image.stem}_clearme_{stamp}"
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
            print(f"Merged Dual BIOS for analysis: {file1} + {file2}", flush=True)
        else:
            if not args.input:
                raise ValueError("Missing --input, or use --dual-file1 and --dual-file2 for Dual BIOS analysis.")
            image = Path(args.input).resolve()
            chip1_size = None

        config = load_config(args.config)
        mea_value = pick_arg(args, config, "mea")
        configured_mea = Path(mea_value).resolve() if mea_value else None
        mea = configured_mea if configured_mea and configured_mea.exists() else find_me_analyzer([Path.cwd()])
        if not mea:
            raise ValueError("ME Analyzer not found. Keep MEA/MEA.py in the project, edit config.json, or pass --mea.")

        print(f"[ANALYZE] Input: {image}", flush=True)
        print(f"[ANALYZE] ME Analyzer: {mea}", flush=True)
        info = analyze_with_mea(mea, image)
        if not info.version or info.major is None:
            raise RuntimeError(
                "ME Analyzer could not detect CSME information in this file. "
                "Check that the selected file is a full BIOS/SPI dump or ME region."
            )
        if info.major < 11 or info.major > 20:
            raise RuntimeError(f"Input CSME major version must be 11-20, got: {info.version}")
        print(f"[ANALYZE] Version: {info.version or 'unknown'}", flush=True)
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
