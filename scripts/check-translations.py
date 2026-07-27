#!/usr/bin/env python3
"""Check every translations/<lang>.json against strings.json.

Home Assistant silently ignores a translation key it does not recognise and silently falls back to
English for one that is missing, so a drifted translation file fails invisibly -- you only find out
when a user reports a half-English dialog. This makes the drift loud instead.

A placeholder mismatch is the one that actually breaks: HA formats these strings with `.format()`,
so a translator who drops `{name}` or renames it to `{nombre}` turns a dialog into a KeyError at
runtime. Both are checked here.

    scripts/check-translations.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "packages/ha-haismart/custom_components/haismart"
PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def flatten(obj: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out


def main() -> int:
    # strings.json is the source of truth; translations/en.json must mirror it exactly.
    reference = flatten(json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8")))
    failures = 0

    for path in sorted((COMPONENT / "translations").glob("*.json")):
        problems: list[str] = []
        try:
            data = flatten(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            print(f"FAIL {path.name}: invalid JSON: {exc}")
            failures += 1
            continue

        if missing := sorted(set(reference) - set(data)):
            problems.append(f"missing {len(missing)} key(s): {', '.join(missing[:5])}")
        if extra := sorted(set(data) - set(reference)):
            problems.append(f"unknown {len(extra)} key(s): {', '.join(extra[:5])}")

        for key in sorted(set(reference) & set(data)):
            want = sorted(PLACEHOLDER.findall(reference[key]))
            got = sorted(PLACEHOLDER.findall(data[key]))
            if want != got:
                problems.append(f"{key}: placeholders {want} became {got}")
            if not str(data[key]).strip():
                problems.append(f"{key}: empty")

        if problems:
            failures += 1
            print(f"FAIL {path.name}")
            for problem in problems:
                print(f"     {problem}")
        else:
            print(f"ok   {path.name}")

    print(f"\n{len(reference)} keys; {failures} file(s) failing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
