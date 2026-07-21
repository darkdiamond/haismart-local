#!/usr/bin/env bash
# Regenerate the HACS-installable integration at the repo root: custom_components/haismart/, with the
# two helper libraries vendored inside it (so `requirements` can be empty — HACS/HA needs no pip step
# for the non-PyPI libs). Run this after changing anything under packages/ and commit the result.
#
#   scripts/build-hacs.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_COMPONENT="$ROOT/packages/ha-haismart/custom_components/haismart"
HRDP="$ROOT/packages/haismart-hrdp/src/haismart_hrdp"
EXTRACTOR="$ROOT/packages/haismart-extractor/src/haismart_extractor"
DEST="$ROOT/custom_components/haismart"

echo "==> regenerating $DEST"
rm -rf "$DEST"
mkdir -p "$DEST/vendor"

# 1. the component itself
cp -r "$SRC_COMPONENT/." "$DEST/"

# 2. vendor the two libraries into ./vendor
cp -r "$HRDP" "$DEST/vendor/haismart_hrdp"
cp -r "$EXTRACTOR" "$DEST/vendor/haismart_extractor"

# 3. strip caches
find "$DEST" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$DEST" -name '*.pyc' -delete 2>/dev/null || true

# 4. manifest: drop the pip requirements (libs are vendored now)
python3 - "$DEST/manifest.json" <<'PY'
import json, sys
p = sys.argv[1]
m = json.load(open(p))
m["requirements"] = []
json.dump(m, open(p, "w"), indent=2)
open(p, "a").write("\n")
PY

# 5. __init__: add the vendor path shim (before any submodule import) so the bundled libs import
python3 - "$DEST/__init__.py" <<'PY'
import sys
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
shim = (
    "\n# HACS/vendored build: bundled helper libs live in ./vendor (no pip step needed). This runs\n"
    "# before any submodule import, so their top-level `from haismart_hrdp import ...` resolve.\n"
    "# ruff: noqa: E402 - the sys.path shim below must precede the submodule imports by design.\n"
    "import os as _os\n"
    "import sys as _sys\n\n"
    "_vendor = _os.path.join(_os.path.dirname(__file__), \"vendor\")\n"
    "if _vendor not in _sys.path:\n"
    "    _sys.path.insert(0, _vendor)\n"
)
anchor = "from __future__ import annotations\n"
if "_vendor" in t:
    pass  # already shimmed
elif anchor in t:
    t = t.replace(anchor, anchor + shim, 1)
else:
    t = shim + t
open(p, "w", encoding="utf-8").write(t)
PY

echo "==> done. Files:"
find "$DEST" -maxdepth 2 -type f | sed "s|$ROOT/||" | sort
