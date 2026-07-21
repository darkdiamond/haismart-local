# Contributing

Thanks for helping improve haismart-local. This is a three-package monorepo (`haismart-hrdp`,
`haismart-extractor`, `ha-haismart`). `packages/haismart-hrdp/src/haismart_hrdp/uss.py` holds the local
protocol (transport, crypto, framing) — read it before changing transport, crypto, or framing code.

## Development setup

```bash
python3 -m venv --system-site-packages .venv && . .venv/bin/activate
pip install pytest pytest-asyncio cryptography ruff
# for the Home Assistant integration tests:
pip install homeassistant pytest-homeassistant-custom-component
```

## Tests & lint (must pass before a PR)

```bash
./scripts/test.sh          # all three suites: haismart-hrdp, haismart-extractor, ha-haismart
ruff check packages        # lint (each package pins its own config)
```

All suites run with **no hardware and no network**. Tests and examples must use **illustrative**
deviceIds/keys — never a real device `localKey`, MAC, or LAN address (see [SECURITY.md](SECURITY.md)).

## Ground rules

- **`uss.py` is the protocol** — the whole local read + control path.
- **No unverified frames to a real AC.** The encoder only emits fields/values in its allowlist
  (`set_grsetdac_field` raises otherwise) — keep it that way: don't widen the grSetDAC map without
  evidence for the new field/value.
- **Never commit secrets.** `*.local.json` and `*.apk` are git-ignored; keep them that way.
- **`custom_components/` at the repo root is generated** — the HACS-installable build with the two
  libraries vendored in. Don't edit it directly: change the source under `packages/`, then run
  `scripts/build-hacs.sh` and commit the regenerated component.
- Match existing style; keep changes focused; add tests for behaviour changes.

## New AC models

Per-model semantics live in `packages/haismart-hrdp/src/haismart_hrdp/profiles.py` as an
`AttributeProfile`, keyed by the cloud `product_code`. `profile_from_device_config()` can self-derive
one from Haier's digital model, so most models need no hand-coding — contribute the `product_code`
mapping and, where possible, a status vector for the test suite.
