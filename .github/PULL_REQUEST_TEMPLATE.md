## What this changes

<!-- One or two sentences. If it fixes an issue, link it. -->

## How it was verified

<!--
Please be specific. For anything touching the protocol, say what you ran it against: which AC
model, and what you observed. "Tests pass" is necessary but not sufficient for a decode or write
change - this project drives real hardware.
-->

- [ ] `./scripts/test.sh` passes
- [ ] `ruff check packages` is clean
- [ ] `scripts/build-hacs.sh` re-run and the regenerated `custom_components/` committed
- [ ] Verified against a real air conditioner (say which model, and how)

## For protocol or write-path changes

- [ ] Any new grSetDAC field or value was **observed on real hardware**, not inferred
- [ ] Behaviour on models other than mine either degrades safely or is gated

<!--
Reminder: never commit a real localKey, device MAC, or LAN address - see SECURITY.md. Test vectors
should use the illustrative placeholders the existing tests use.
-->
