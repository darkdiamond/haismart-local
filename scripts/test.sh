#!/usr/bin/env bash
# Run every package's test suite. No hardware, no network.
# The ha-haismart suite skips cleanly unless Home Assistant + the HA pytest plugin are installed.
set -u
root="$(cd "$(dirname "$0")/.." && pwd)"
py="${PYTHON:-python3}"
fail=0
for pkg in haismart-hrdp haismart-extractor ha-haismart; do
  echo "=== $pkg ==="
  ( cd "$root/packages/$pkg" && "$py" -m pytest -q -p no:cacheprovider ) || fail=1
  echo
done
exit $fail
