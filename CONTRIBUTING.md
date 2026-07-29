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
- **Only surface a reading you've confirmed is real.** The status and extended reports contain more
  fields than the integration exposes. A field becomes an entity only when it's been seen to behave
  correctly on real hardware — several were left out because they didn't (e.g. an outdoor-fan state
  that reads "on" while the unit is off). And a reading a unit doesn't actually have must decode to
  *unavailable*, never a fabricated value: a missing temperature reads `0`, which naive maths turns
  into a confident −64 °C that then poisons long-term statistics. `parse_extended_status` and the
  temperature helpers already guard this; keep new fields to the same bar. The extended report's byte
  offsets are **per report family** — the ones in `parse_extended_status` are for the classic
  (141-byte) family, so another family needs its own offsets confirmed before it can decode there.
- **Never commit secrets.** `*.local.json` and `*.apk` are git-ignored; keep them that way.
- **`custom_components/` at the repo root is generated** — the HACS-installable build with the two
  libraries vendored in. Don't edit it directly: change the source under `packages/`, then run
  `scripts/build-hacs.sh` and commit the regenerated component.

  This is easy to get wrong, so three things now catch it. `scripts/build-hacs.sh` prints what it
  discarded if you had edited the generated tree; `scripts/check-hacs-build.sh` (also run by
  `scripts/test.sh`) fails if the committed tree does not match what the build would produce; and CI
  runs the same comparison. The suites alone will not catch it — they import the `packages/` copy, so
  a stale or hand-edited generated tree keeps them green.

  **On a merge conflict inside `custom_components/`, do not hand-resolve it.** It is a generated
  file, so a hand-merge produces a tree matching neither side. Take either version
  (`git checkout --ours` / `--theirs`), then re-run `scripts/build-hacs.sh` and commit the result.
- Match existing style; keep changes focused; add tests for behaviour changes.

## Entity names and translations

A new named entity (a `translation_key` on a sensor, switch, etc.) needs a matching string in
`packages/ha-haismart/custom_components/haismart/strings.json` **and** in every file under
`translations/`. The integration ships ~30 languages and `scripts/check-translations.py` enforces
strict key parity — a key present in some files but not others fails, so adding it to `en.json`
alone will not do.

```bash
python3 scripts/check-translations.py    # key parity + placeholder integrity across every language
```

If you can't provide a real translation for a language, use the English string for now (present and
non-empty passes the check); a later PR can localise it. Diagnostic engineering entities can instead
skip `translation_key` and lean on their device class, which Home Assistant already translates — but
only when the device-class name is unambiguous (several temperatures all called "Temperature" is not,
so those carry a `translation_key`).

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
