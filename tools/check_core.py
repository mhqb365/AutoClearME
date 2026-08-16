#!/usr/bin/env python3
"""Small smoke checks for behavior that refactors should not change."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from AutoClearME import FirmwareInfo, detect_asus_bios_header, detect_bios_version, display_sku, find_acer_dmi, find_asus_dmi, parse_mea_output, score_rgn, sku_matches
from AutoClearME_GUI import format_version, version_parts


MEA_SAMPLE = """
Family: CSE ME
Version: 15.0.35.1898
Release: Production
Type: Extracted
SKU: Consumer LP
Chipset: TGP-LP B
FIT: 15.0.10.1432
File System: Initialized
"""


def main() -> int:
    info = parse_mea_output(MEA_SAMPLE)
    assert info.version == "15.0.35.1898"
    assert info.major == 15
    assert info.minor == 0
    assert info.family == "CSE ME"
    assert info.sku == "consumer lp"
    assert info.chipset == "TGP-LP B"
    assert info.fit == "15.0.10.1432"
    assert info.data_state == "Initialized"
    assert display_sku("consumer lp") == "Consumer LP"
    assert sku_matches("Consumer LP", "CON_LP")
    input_info = FirmwareInfo(version="14.1.70.2228", major=14, minor=1, sku="corporate h")
    old_rgn_score, _ = score_rgn(input_info, Path("14.1.53.1649_COR_H_A_PRD_RGN.bin"))
    exact_extr_score, _ = score_rgn(input_info, Path("14.1.70.2228_COR_H_A_PRD_EXTR-Y_B430BC4A.bin"))
    assert exact_extr_score > old_rgn_score
    assert version_parts("v1.01") == (1, 0, 1, 0)
    assert version_parts("v1.0.1") == (1, 0, 1, 0)
    assert version_parts("v1.0.8.1") == (1, 0, 8, 1)
    assert format_version("v1.01") == "1.0.1"
    assert format_version("v1.0.8.1") == "1.0.8.1"
    bios_samples = {
        "Dell": b"Dell Inc.\x00BIOS Version\x001.32.0\x00CSME 16.1.38.2676\x00",
        "Lenovo": b"LENOVO\x00ThinkPad\x00N3HET76W (1.48 )\x00",
        "HP": b"Hewlett-Packard\x00BIOS Version\x00S70 Ver. 01.17.00\x00",
        "Acer": b"Acer Incorporated\x00BIOS Version\x00V1.28\x00",
        "ASUS": b"ASUSTeK COMPUTER INC.\x00BIOS Version\x00310\x00",
    }
    for vendor, sample in bios_samples.items():
        detected_vendor, detected_version = detect_bios_version(sample)
        assert detected_vendor == vendor
        assert detected_version
    assert detect_bios_version(b"Dell Inc.\x00CSME 16.1.38.2676\x00") == ("Dell", "")
    asus_header = b"$MODIFYSIG$\x003\x00\x00\x00UX425EA\x00\x00\x00\x00\x0016\x00\x0006/10/2022"
    assert detect_asus_bios_header(asus_header) == ("UX425EA", "316")
    assert detect_bios_version(asus_header) == ("ASUS", "316")
    asus_mfg = bytearray(b"\xFF" * 0x104)
    for offset, value in (
        (0x00, b"MFG0\x00"),
        (0x05, b"N1N0LP010226016"),
        (0x1E, b"90NB0SM1-M006V0"),
        (0x32, b"MC51NBLP003B0AMB"),
        (0x55, b"QCCXKP6BD01703586"),
        (0x69, b"0301A"),
        (0x85, b"UX425EA"),
        (0x99, b"2022-01-10 18:31:28"),
    ):
        asus_mfg[offset:offset + len(value)] = value
    asus_items = {(item.label, item.value) for item in find_asus_dmi(bytes(asus_mfg))}
    assert ("Board Serial Number", "N1N0LP010226016") in asus_items
    assert ("System Identifier", "QCCXKP6BD01703586") in asus_items
    assert ("Configuration ID", "0301A") in asus_items
    assert ("Model Identifier", "UX425EA") in asus_items
    acer_block = bytearray(0x2000)
    acer_block[0x100:0x100 + 57] = b"Acer\x00Aspire A315-58\x00NXHS5AA00123456789ABC\x0012345678901\x00"
    acer_items = {(item.label, item.value) for item in find_acer_dmi(bytes(acer_block))}
    assert ("Vendor", "Acer") in acer_items
    assert ("Model", "Aspire A315-58") in acer_items
    assert ("Serial Number", "NXHS5AA00123456789ABC") in acer_items
    assert ("SNID", "12345678901") in acer_items
    print("core smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
