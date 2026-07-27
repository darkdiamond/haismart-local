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

## Adding a new AC model

Most models need **no code at all** — the integration derives its behaviour from the device's own
digital model. When something does need a change, the work is almost always one table entry, and the
hard part is the evidence, not the patch.

Start from [`docs/new-model.md`](docs/new-model.md): three status captures in known states pin the
control-word block and identify the sensor bytes by elimination. If you have the unit in front of
you, that is the fastest path to a correct layout.

Two rules make this safe:

- **Reads may be widened on inference; writes may not.** An unknown report length is decoded as far
  as the layout-independent fields allow and flagged `partial`. `STATUS_LAYOUTS` stays the allowlist
  for writes, because a wrong word count sends a sensor byte back to the AC as a control word.
- **A new grSetDAC field or value needs an observation, not a deduction.** The way to get one is a
  single-attribute sweep: change exactly one setting in the vendor app and diff the report. That is
  how every field in the current map was established, and how horizontal swing and heat were added.

## New AC models

Per-model semantics live in `packages/haismart-hrdp/src/haismart_hrdp/profiles.py` as an
`AttributeProfile`, keyed by the cloud `product_code`. `profile_from_device_config()` can self-derive
one from Haier's digital model, so most models need no hand-coding — contribute the `product_code`
mapping and, where possible, a status vector for the test suite.
