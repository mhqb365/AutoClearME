#!/usr/bin/env python3
"""Small smoke checks for behavior that refactors should not change."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from AutoClearME import display_sku, parse_mea_output, sku_matches


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
    print("core smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
