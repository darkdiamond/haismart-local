#!/usr/bin/env bash
# Verify that custom_components/ matches what scripts/build-hacs.sh would generate from packages/.
#
# custom_components/ is a generated, committed tree (HACS installs from the repo root, so it has to
# be committed). Two ways it drifts:
#   * someone edits packages/ and forgets to re-run the build, so the shipped component is stale;
#   * someone edits custom_components/ directly, and the next build silently reverts it.
# Both leave the test suites green, because the suites import the packages/ copy. CI fails on this;
# this script exists so you find out before pushing.
#
# It is a CHECK: it leaves your working tree exactly as it found it, and never commits anything.
#
#   scripts/check-hacs-build.sh          # exit 0 in sync, 1 if it drifted
set -uo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/custom_components"
[ -d "$dest" ] || { echo "no custom_components/ to check"; exit 0; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp -r "$dest" "$tmp/actual"

# QUIET_BUILD keeps the build's own "discarded local changes" advice out of a check that is about to
# put those same changes back.
QUIET_BUILD=1 "$root/scripts/build-hacs.sh" >/dev/null 2>&1
cp -r "$dest" "$tmp/expected"

# Restore what was there before, whatever the verdict — a check must not mutate the tree.
rm -rf "$dest"
cp -r "$tmp/actual" "$dest"

if diff -rq "$tmp/actual" "$tmp/expected" >/dev/null 2>&1; then
  echo "custom_components/ is in sync with packages/"
  exit 0
fi

{
  echo "custom_components/ is NOT in sync with packages/:"
  diff -rq "$tmp/actual" "$tmp/expected" 2>&1 | sed "s|$tmp/actual|committed|; s|$tmp/expected|generated|; s/^/  /"
  echo
  echo "Fix: run scripts/build-hacs.sh and commit the result."
  echo
  echo "If you were changing the component, edit the SOURCE, never the generated tree:"
  echo "    the component  -> packages/ha-haismart/custom_components/haismart/"
  echo "    the helper libs -> packages/haismart-hrdp/src, packages/haismart-extractor/src"
} >&2
exit 1
