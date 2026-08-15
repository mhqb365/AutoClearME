#!/usr/bin/env python3
"""Validate the languages.json structure and translation keys."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    data = json.loads((ROOT / "languages.json").read_text(encoding="utf-8-sig"))
    languages = data.get("text", {})
    errors = []
    if not isinstance(data.get("labels"), dict) or not data["labels"]:
        errors.append("labels must be a non-empty object")
    if not isinstance(languages, dict) or "en" not in languages:
        errors.append("text.en is required")
    else:
        english_keys = set(languages["en"])
        for code, translations in languages.items():
            if not isinstance(translations, dict):
                errors.append(f"text.{code} must be an object")
                continue
            if missing := sorted(english_keys - set(translations)):
                errors.append(f"{code}: missing translations: {missing}")
    if errors:
        print("\n".join(errors))
        return 1
    print("languages.json is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
