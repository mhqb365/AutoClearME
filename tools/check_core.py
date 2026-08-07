#!/usr/bin/env python3
"""Small smoke checks for behavior that refactors should not change."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from AutoClearME import FirmwareInfo, display_sku, find_acer_dmi, parse_mea_output, score_rgn, sku_matches
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
    assert version_parts("v1.01") == (1, 0, 1)
    assert version_parts("v1.0.1") == (1, 0, 1)
    assert format_version("v1.01") == "1.0.1"
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
