#!/usr/bin/env bash
# Run every package's test suite. No hardware, no network.
# The ha-haismart suite SKIPS ITSELF unless Home Assistant + pytest-homeassistant-custom-component
# are installed, so a green run here does not prove it ran. CI installs both and fails the job if
# that suite collects nothing; locally, install them if you are touching the integration:
#   pip install homeassistant pytest-homeassistant-custom-component zeroconf
set -u
root="$(cd "$(dirname "$0")/.." && pwd)"
py="${PYTHON:-python3}"
fail=0
for pkg in haismart-hrdp haismart-extractor ha-haismart; do
  echo "=== $pkg ==="
  ( cd "$root/packages/$pkg" && "$py" -m pytest -q -p no:cacheprovider ) || fail=1
  echo
done
# custom_components/ is generated from packages/ and committed for HACS. The suites above import
# the packages/ copy, so a stale or hand-edited generated tree passes them all. Check it here too,
# rather than letting CI be the first thing that notices.
echo "=== HACS build in sync ==="
"$root/scripts/check-hacs-build.sh" || fail=1

exit $fail
