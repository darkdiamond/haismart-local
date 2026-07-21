# haismart-hrdp

Standalone, async, fully-typed Python client for Haier's local **uSS/HRDP** protocol (Haismart / U+
SE-Asia ACs). No Home Assistant coupling, no cloud.

## What it does

- Plaintext handshake on TCP `:56800`, then AES-128-CBC biz-data (key = `MD5(localKey)`).
- **Read:** `read_status` / `async_read_status` → `parse_full_status` decodes power, target / indoor /
  outdoor temperature, mode, fan, swing, and the secondary toggles.
- **Control:** the `grSetDAC` group-set write path (`grsetdac_baseline_from_status` →
  `set_grsetdac_field` → `async_send_op`). The encoder only emits fields/values in its allowlist.
- Per-model semantics via `AttributeProfile`, built from the device digital model (`profiles.py`).

## Example (read)

```python
import haismart_hrdp as h
blob = next(b for b in h.read_status("192.168.1.50", "ACB722AABBCC", "<localKey>") if len(b) == 127)
print(h.parse_full_status(blob, h.profile_for("AAC1UKZ01")))
```

## Tests

```bash
python -m pytest
```
