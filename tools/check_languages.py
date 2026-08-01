#!/usr/bin/env python3
"""Check that GUI fallback text stays in sync with languages.json."""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_fallback_text() -> dict:
    source = (ROOT / "AutoClearME_GUI.py").read_text(encoding="utf-8-sig")
    module = ast.parse(source)
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "FALLBACK_TEXT" for target in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError("FALLBACK_TEXT was not found.")


def main() -> int:
    fallback = load_fallback_text()
    languages = json.loads((ROOT / "languages.json").read_text(encoding="utf-8-sig"))["text"]
    errors = []
    for code in sorted(set(fallback) | set(languages)):
        fallback_keys = set(fallback.get(code, {}))
        language_keys = set(languages.get(code, {}))
        if missing := sorted(fallback_keys - language_keys):
            errors.append(f"{code}: missing in languages.json: {missing}")
        if missing := sorted(language_keys - fallback_keys):
            errors.append(f"{code}: missing in FALLBACK_TEXT: {missing}")
        for key in sorted(fallback_keys & language_keys):
            if fallback[code][key] != languages[code][key]:
                errors.append(f"{code}.{key}: fallback differs from languages.json")
    if errors:
        print("\n".join(errors))
        return 1
    print("FALLBACK_TEXT matches languages.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
