#!/usr/bin/env python3
"""Small smoke checks for behavior that refactors should not change."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from AutoClearME import FirmwareInfo, WinKeyCandidate, build_parser, detect_asus_bios_header, detect_bios_version, display_sku, find_acer_dmi, find_asus_dmi, find_dell_dmi, format_winkey_candidate, hp_dmi_label, is_hp_model, lenovo_dmi_label, merge_bios_files, parse_bios_mb_size, parse_mea_output, patch_winkey, score_rgn, sku_matches, split_bios_file, unlock_dell_8fc8_password, update_acpi_table_checksum
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
    winkey_line = format_winkey_candidate(WinKeyCandidate("ACPI MSDM", 0x1234, "JJQTN-6996D-TX6B2-RFVBH-PWF9C", classification="Win 10 RTM Professional OEM:DM, EULA OEM"))
    assert "Offset: [0x1234, 0x1251]" in winkey_line
    assert "JJQTN-6996D-TX6B2-RFVBH-PWF9C | Win 10 RTM Professional OEM:DM, EULA OEM" in winkey_line
    assert parse_bios_mb_size("8") == 8 * 1024 * 1024
    assert parse_bios_mb_size("8MB") == 8 * 1024 * 1024
    parser = build_parser()
    for command_name in ("lenovo-dmi", "asus-dmi", "hp-dmi", "acer-dmi", "dell-dmi"):
        args = parser.parse_args([command_name, "--input", "target.bin"])
        assert args.func
    for command_name in ("lenovo-dmi-export", "asus-dmi-export", "hp-dmi-export", "acer-dmi-export", "dell-dmi-export"):
        args = parser.parse_args([command_name, "--input", "target.bin"])
        assert args.func
    for command_name in ("lenovo-dmi-import", "asus-dmi-import", "hp-dmi-import", "acer-dmi-import", "dell-dmi-import"):
        args = parser.parse_args([command_name, "--dmi", "package.dmi", "--target", "target.bin"])
        assert args.func
    args = parser.parse_args(["winkey-patch", "--input", "target.bin", "--key", "VK7JG-NPHTM-C97JM-9MPGT-3V66T"])
    assert args.func
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
    dell_identity = bytearray(0x100)
    dell_ppid = b"CN00VPNPCMC0011K0EDCA00"
    dell_identity[0x10:0x10 + len(dell_ppid)] = dell_ppid
    dell_identity[0x30:0x37] = b"5M6YGB3"
    dell_items = {(item.label, item.value) for item in find_dell_dmi(bytes(dell_identity))}
    assert ("Service Tag", "5M6YGB3") in dell_items
    assert ("PPID", "CN00VPNPCMC0011K0EDCA00") in dell_items
    dell_model_block = b"$DMI" + b"\x00" * 16 + b"Inspiron 3505\x00XPS]K\x00"
    dell_model_items = {(item.label, item.value) for item in find_dell_dmi(dell_model_block)}
    assert ("Model", "Inspiron 3505") in dell_model_items
    assert ("Model", "XPS]K") not in dell_model_items
    dell_8fc8_header = bytes.fromhex("5A A5 F0 0F 03") + b"\xFF" * 8
    locked_8fc8 = dell_8fc8_header + (
        bytes.fromhex("00 FD AA 30 00 00 00 00 04 00 FF")
        + b"\xFF" * 8
        + bytes.fromhex("00 FC AA 31 00 00 00 00 04 00 FF")
    )
    unlocked_8fc8 = locked_8fc8.replace(b"\xAA\x30", b"\x00\x30").replace(b"\xAA\x31", b"\x00\x31")
    with tempfile.TemporaryDirectory() as temp_dir:
        locked_path = Path(temp_dir) / "locked.bin"
        locked_path.write_bytes(locked_8fc8)
        output, cleared_ranges = unlock_dell_8fc8_password(locked_path)
        assert output is not None
        assert output.read_bytes() == unlocked_8fc8
        assert cleared_ranges == [(len(dell_8fc8_header) + 2, 1), (len(dell_8fc8_header) + 21, 1)]
        unlocked_path = Path(temp_dir) / "unlocked.bin"
        unlocked_path.write_bytes(unlocked_8fc8)
        output, cleared_ranges = unlock_dell_8fc8_password(unlocked_path)
        assert output is None
        assert cleared_ranges == []
        service_tag_path = Path(temp_dir) / "service-tag-path.bin"
        service_tag_path.write_bytes(dell_8fc8_header + bytes.fromhex("00 FD AA 31 00 00 00 00 00 00 FF"))
        output, cleared_ranges = unlock_dell_8fc8_password(service_tag_path)
        assert output is not None
        assert output.read_bytes() == dell_8fc8_header + bytes.fromhex("00 FD 00 31 00 00 00 00 00 00 FF")
        assert cleared_ranges == [(len(dell_8fc8_header) + 2, 1)]
        service_tag_unlocked_path = Path(temp_dir) / "service-tag-path-unlocked.bin"
        service_tag_unlocked_path.write_bytes(dell_8fc8_header + bytes.fromhex("00 FD 00 31 00 00 00 00 00 00 FF"))
        output, cleared_ranges = unlock_dell_8fc8_password(service_tag_unlocked_path)
        assert output is None
        assert cleared_ranges == []
    dell_8fc8_locked_sample = Path("bios/c28v5y2.BIN")
    dell_8fc8_unlocked_sample = Path("bios/T&C_patched_c28v5y2.BIN")
    if dell_8fc8_locked_sample.exists() and dell_8fc8_unlocked_sample.exists():
        with tempfile.TemporaryDirectory() as temp_dir:
            locked_copy = Path(temp_dir) / dell_8fc8_locked_sample.name
            locked_copy.write_bytes(dell_8fc8_locked_sample.read_bytes())
            output, cleared_ranges = unlock_dell_8fc8_password(locked_copy)
            assert output is not None
            assert [offset for offset, length in cleared_ranges if length == 1] == [0x47003, 0x470BF, 0x48ECB, 0x48F77]
            assert output.read_bytes() == dell_8fc8_unlocked_sample.read_bytes()
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        winkey_bios = temp_root / "winkey.bin"
        winkey_bios.write_bytes(b"\xFF" * 0x20 + b"JJQTN-6996D-TX6B2-RFVBH-PWF9C" + b"\xFF" * 0x20)
        winkey_output, patched_keys = patch_winkey(winkey_bios, "VK7JG-NPHTM-C97JM-9MPGT-3V66T")
        assert [(candidate.offset, checksum_updated) for candidate, checksum_updated in patched_keys] == [(0x20, False)]
        assert b"VK7JG-NPHTM-C97JM-9MPGT-3V66T" in winkey_output.read_bytes()
        assert b"JJQTN-6996D-TX6B2-RFVBH-PWF9C" not in winkey_output.read_bytes()
        mirrored_winkey_bios = temp_root / "mirrored-winkey.bin"
        mirrored_winkey_bios.write_bytes(
            b"\xFF" * 0x20
            + b"JJQTN-6996D-TX6B2-RFVBH-PWF9C"
            + b"\xFF" * 0x20
            + b"JJQTN-6996D-TX6B2-RFVBH-PWF9C"
        )
        mirrored_output, patched_keys = patch_winkey(mirrored_winkey_bios, "VK7JG-NPHTM-C97JM-9MPGT-3V66T")
        assert [candidate.offset for candidate, _checksum_updated in patched_keys] == [0x20, 0x5D]
        assert mirrored_output.read_bytes().count(b"VK7JG-NPHTM-C97JM-9MPGT-3V66T") == 2
        assert b"JJQTN-6996D-TX6B2-RFVBH-PWF9C" not in mirrored_output.read_bytes()
        msdm_bios = temp_root / "msdm.bin"
        msdm_table = bytearray(
            b"MSDM"
            + (0x55).to_bytes(4, "little")
            + b"\x01\x00"
            + b"OEMID "
            + b"OEMTABLE"
            + b"\x01\x00\x00\x00"
            + b"TEST"
            + b"\x01\x00\x00\x00"
            + b"\x01\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x1D\x00\x00\x00"
            + b"JJQTN-6996D-TX6B2-RFVBH-PWF9C"
        )
        update_acpi_table_checksum(msdm_table, 0, len(msdm_table))
        msdm_bios.write_bytes(b"\xFF" * 0x40 + msdm_table + b"\xFF" * 0x20)
        msdm_output, patched_keys = patch_winkey(msdm_bios, "VK7JG-NPHTM-C97JM-9MPGT-3V66T")
        patched_msdm = msdm_output.read_bytes()[0x40:0x40 + len(msdm_table)]
        assert len(patched_keys) == 1
        assert patched_keys[0][1]
        assert sum(patched_msdm) & 0xFF == 0
        bios1 = temp_root / "bios1.bin"
        bios2 = temp_root / "bios2.bin"
        bios1.write_bytes(b"\x11" * (1024 * 1024) + b"meta")
        bios2.write_bytes(b"\x22" * (1024 * 1024))
        merged, inputs = merge_bios_files(bios1, bios2)
        assert merged.name == "2MB_MERGED.bin"
        assert merged.read_bytes() == b"\x11" * (1024 * 1024) + b"\x22" * (1024 * 1024)
        assert inputs[0][2] == 4
        chip1, chip2, excess = split_bios_file(merged, 1024 * 1024, 1024 * 1024)
        assert chip1.read_bytes() == b"\x11" * (1024 * 1024)
        assert chip2.read_bytes() == b"\x22" * (1024 * 1024)
        assert excess == 0
    assert hp_dmi_label("AAAAA-BBBBB-CCCCC-DDDDD-EEEEE") == "Windows Product Key"
    assert is_hp_model("HP ProBook 450 G8 Notebook")
    assert not is_hp_model("HP Linux Installer")
    print("core smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
