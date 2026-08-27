#!/usr/bin/env python3
"""Small smoke checks for behavior that refactors should not change."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from AutoClearME import FirmwareInfo, detect_asus_bios_header, detect_bios_version, display_sku, find_acer_dmi, find_asus_dmi, lenovo_dmi_label, parse_mea_output, score_rgn, sku_matches
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
Release: Production
TCB Security Version Number: 3
Version Control Number: 331
Production Ready: Yes
Workstation Support: No
OEM Configuration: No
Date: 2022-03-21
Size: 0x630000
Chipset Support: ADP-LP
MEA Database Name: 15.0.35.1898_COR_H
MEA Support Status: Yes
RSA Signature Hash: 0123456789ABCDEF
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
    assert info.release == "Production"
    assert info.tcb_svn == "3"
    assert info.vcn == "331"
    assert info.production_ready == "Yes"
    assert info.workstation_support == "No"
    assert info.oem_configuration == "No"
    assert info.date == "2022-03-21"
    assert info.size == "6.19 MB"
    assert info.chipset_support == "ADP-LP"
    assert info.mea_database_name == "15.0.35.1898_COR_H"
    assert info.mea_support_status == "Yes"
    assert info.rsa_signature_hash == "0123456789ABCDEF"
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
    old_asus_mfg = bytearray(b"\xFF" * 0x104)
    for offset, value in (
        (0x00, b"MFG0\x00"),
        (0x05, b"H4N0CV09J663168"),
        (0x1E, b"90NB0DL2-M00740"),
        (0x32, b"N0CV1716MB0068401"),
        (0x55, b"BN13"),
        (0x69, b"UX410UAK.3"),
        (0x73, b"01"),
    ):
        old_asus_mfg[offset:offset + len(value)] = value
    old_asus_items = {(item.label, item.value) for item in find_asus_dmi(bytes(old_asus_mfg))}
    assert ("Board Serial Number", "H4N0CV09J663168") in old_asus_items
    assert ("Model Identifier", "UX410UAK") in old_asus_items
    bad_asus_mfg = bytearray(b"\xFF" * 0x104)
    for offset, value in (
        (0x00, b"MFG0\x00"),
        (0x05, b"J9ORCX08X:7=;>7"),
        (0x1E, b"98NR80I1-O8<998"),
        (0x32, b"YcCBKn;BZ<;583;79"),
        (0x85, b"X=84GE"),
    ):
        bad_asus_mfg[offset:offset + len(value)] = value
    assert not find_asus_dmi(bytes(bad_asus_mfg))
    acer_block = bytearray(0x2000)
    acer_block[0x100:0x100 + 57] = b"Acer\x00Aspire A315-58\x00NXHS5AA00123456789ABC\x0012345678901\x00"
    acer_block[0x180:0x19A] = b"Acer Root CA0\x00Acer Database0"
    acer_items = {(item.label, item.value) for item in find_acer_dmi(bytes(acer_block))}
    assert ("Vendor", "Acer") in acer_items
    assert ("Model", "Aspire A315-58") in acer_items
    assert ("System Serial Number", "NXHS5AA00123456789ABC") in acer_items
    assert ("SNID", "12345678901") in acer_items
    assert ("Vendor", "Acer Root CA0") not in acer_items
    prefixed_acer = b"Acer\x00-HAspire A315-24P\x00NXKJBAA001320012693400\x00"
    prefixed_acer_items = {(item.label, item.value) for item in find_acer_dmi(prefixed_acer)}
    assert ("Model", "Aspire A315-24P") in prefixed_acer_items
    assert ("System Serial Number", "NXKJBAA001320012693400") in prefixed_acer_items
    board_acer_items = {(item.label, item.value) for item in find_acer_dmi(b"Acer\x00NBKTV1100241200AE04560\x00")}
    assert ("Board Serial Number", "NBKTV1100241200AE04560") in board_acer_items
    assert not find_acer_dmi(b"Acer Root CA0\x00Acer Platform Key0\x00Acer Database0\x00")
    assert lenovo_dmi_label("83AM0002CD") == "MTM"
    assert lenovo_dmi_label("XiaoXinPro 14 APH8") == "Product Name"
    assert lenovo_dmi_label("WIN") == "OS"
    assert lenovo_dmi_label("SDK0T76479") == "Platform ID"
    print("core smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
