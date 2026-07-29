# The uSS local protocol

Reference notes for the LAN protocol these air conditioners speak on **TCP port 56800**. Useful if
you are adding a model, debugging a decode, or driving a unit without Home Assistant.

The protocol itself was reverse-engineered by [@enapt](https://github.com/enapt); this document
mostly records what the code already encodes, plus what has been learned since.

## Layers

1. **uSS message** — a 16-byte header plus payload:

   ```
   [0:4]   info_code BE32 = 0xEA60 + info_type  (hello=0, hello_resp=1, hello_done=2, done_resp=3)
   [4:6]   payload_len + 0x0A (BE16)
   [6]     type byte      (pro_ver 2 -> 0x01, pro_ver 3 -> 0x6E)
   [7]     flag           (0 plaintext, 1 encrypted biz-data)
   [8:12]  sn BE32        (client counter from 1; the AC echoes it)
   [12:14] code2 BE16
   [14:16] session BE16   (0 in the client hello; the AC ASSIGNS one in HELLO_RESP)
   ```

   A declared length below `0x0A` cannot be a real frame — the header alone is 16 bytes.

2. **Handshake** (plaintext): client `HELLO` → AC `HELLO_RESP` → client `HELLO_DONE` → AC
   `HELLO_DONE_RESP`, after which the AC pushes status as `0xEAC4` messages.

   `HELLO_RESP`'s payload is `status(BE32) || localkey_version(BE32)`. **`status` must be 1.** A unit
   that answers with a different status has refused the session: it will accept `HELLO_DONE`, push
   nothing, and look exactly like a dead network. The version field is free rotation detection — no
   separate probe connection is needed.

3. **biz-data payload**: AES-128-CBC, IV = 16 zero bytes, key = `MD5(localKey-as-ascii-hex)`. The
   plaintext carries an `sn` and an MD5 integrity check. A wrong or rotated key fails that MD5 on
   every payload — silently, which is why a stale key and an unreadable layout used to be
   indistinguishable.

Sessions are capped at roughly **17 seconds** from the handshake (not an idle timer — a keepalive
does not extend it), and the AC accepts **one local session at a time**. It delivers its whole status
burst immediately and then holds the socket open and silent, so a collector should return on a short
idle window rather than waiting out the full timeout.

## The status report

A decrypted full-status blob is:

```
[0:78]    CAE report prefix   (identical across models; [2:4] == 27 15 identifies it)
[78:80]   inner frame length  (BE16)
[80:]     EPP frame:  ff ff | len | flags | 5 reserved | type | data | checksum
```

The packed attribute vector always begins at **byte 92**, immediately after the `6d 01`
getAllProperty response code. What varies by model is how many grSetDAC **control words** (2 bytes
each) precede the read-only sensor block:

| Report length | Control words | indoorTemperature | outdoorTemperature |
|---|---|---|---|
| 127 bytes | 6 | byte 104 | byte 106 |
| 125 bytes | 5 | byte 102 | byte 104 |

Both satisfy `indoor = 92 + 2 * words` and `outdoor = indoor + 2`, and the trailing block (sensors
plus checksum) is 23 bytes on both — so `words = (length - 115) / 2`. The library uses that closed
form to *read* an unknown layout, vetoed by a plausibility check on the byte it would call
indoorTemperature, but keeps the confirmed table as the allowlist for *writes*: a wrong word count
would send a sensor byte back to the AC as a control word.

Fields at bytes 92–97 are control words 1–3 and therefore sit before anything the word count shifts,
which is why an unrecognised report can still be partially decoded.

### Other wire families (per-model layouts)

The table above is the *classic* family. Some models pack their attributes into an entirely
different layout — not just a different word count, but different words for each attribute, and the
sensors interleaved into the same array rather than in a trailing block. Each such family is a
**wire model**: a per-attribute map of `word` / `bit` / `length` plus the value transforms, kept in
[`wire_models.py`](packages/haismart-hrdp/src/haismart_hrdp/wire_models.py) and selected by uPlusId
(the cloud device-list `wifiType`) or, failing that, by report length — with a plausibility check so
an ambiguous length falls back to the unknown-layout path instead of mis-decoding.

The first such family is **compact-12** (117-byte report, e.g. HSU-12HFMF): a 12-word array with
`indoorTemperature` at word 1, `operationMode` at word 6, `windSpeed` at word 7, swings at word 8,
`onOffStatus` at word 9 and `targetTemperature` at word 12. Its enum values are true EPP indices
(unlike the classic family, whose stored codes equal the Haier STD codes), so the wire model maps
each back to its STD code for the profile to name. Control uses the model's own group-set command
(`4d5f`, vs the classic `6001`), a read-modify-write over the same 12-word array.

The second is **extended-36** (165-byte report, e.g. HSU-12KCROC(IN)-R32, `deviceType 02012036`).
This one is not a different bit map at all: it is the *classic* map **displaced by 19 words**. The
report begins with a voice/media module block (volume, playback, dialect …) that the model's generic
preset describes but a plain split AC leaves inert, and the climate attributes follow it —
`targetTemperature` at word 20 bit 8, `operationMode` at word 21 bit 13, `windSpeed` at word 21
bit 8, the boolean block at word 22, `windDirectionHorizontal` at word 23, `indoorTemperature` at
word 25 bit 8 and `outdoorTemperature` at word 26 bit 8. That displacement is exactly why the classic
*partial* decode misfires on this model rather than simply falling short: byte 92 is the media
block's `volume`, which reads as a 48 °C setpoint.

Its control path is the classic `6001` group-set with the classic five-word bit map — the op is
unchanged; only the *baseline* is sliced from report word 20 instead of word 1. That displacement is
what `WireModel.write_base_word` expresses: where a family keeps its control block in the report is
independent of where that block sits in the op.

> **Future consolidation.** The classic family is currently a bespoke decoder/encoder while the newer
> families are data-driven wire models — two paradigms for the same idea. The plan, once the wire-model
> path has more confirmed models behind it, is to fold the classic family into the registry as one more
> entry (extending `WireModel` to cover its byte-offset sensors, variable 125/127 length, and
> device-specific values such as the swing toggle and repurposed eco field), collapsing to a single
> path. A smaller first step is to extract the shared bit pack/unpack helpers the two paths currently
> duplicate. Both are deliberately deferred so the hardware-verified classic path stays untouched until
> then.

### Confirmed field offsets

| Field | Where | Decode |
|---|---|---|
| `targetTemperature` | byte 92 | `byte + 16` |
| `windDirectionVertical` | byte 93 | bit 3 (`0x08`) = auto up-down swing |
| `operationMode` / `windSpeed` | byte 94 | `byte >> 5` and `byte & 0x07` |
| `onOffStatus` | byte 97 | non-zero = on |
| `windDirectionHorizontal` | word 4, bits 0–2 | `0` fixed, `7` auto |
| `indoorTemperature` | per layout | `byte / 2` |
| `outdoorTemperature` | per layout | `byte - 64` |

`windSpeed` is masked with `0x07`, not `0x0F`: bit 3 of byte 94 belongs to `specialMode`, so a wider
mask invents a fan code of `speed + 8`.

A unit without a given sensor reports `0`, which the raw outdoor formula turns into a confident
−64 °C. Absent sensors must decode to *absent*, not to a fabricated reading.

## grSetDAC control words

Writes are a **group-set**: the whole word block is sent at once, so it must be seeded from the AC's
own current status or unrelated settings get clobbered. The library seeds from the status the AC
pushes on the op's own connection, so the baseline is always live.

| Field | Word | Shift | Width |
|---|---|---|---|
| `targetTemperature` | 1 | 8 | 8 |
| `windDirectionVertical` | 1 | 0 | 4 |
| `operationMode` | 2 | 13 | 3 |
| `windSpeed` | 2 | 8 | 3 |
| `onOffStatus` | 3 | 0 | 1 |
| `healthMode` | 3 | 1 | 1 |
| `rapidMode` | 3 | 3 | 1 |
| `muteStatus` | 3 | 4 | 1 |
| `silentSleepStatus` | 3 | 5 | 1 |
| `screenDisplayStatus` | 3 | 9 | 1 |
| `windDirectionHorizontal` | 4 | 0 | 3 |
| `ecoMode` (unconfirmed) | 4 | 3 | 3 |

STD operation-mode codes are drawn from a Haier-wide space, not allocated per model — the tell is the
gaps: a cooling-only unit lists 0/1/2 then jumps to 6, skipping 3/4/5. Known codes: `0` auto, `1`
cool, `2` dry, `4` heat, `6` fan-only. Wind speed: `1` high, `2` medium, `3` low, `5` auto.

**`ecoMode` is not confirmed.** There is no `ecoMode` attribute in the digital model, and word 4
bits 3–5 most likely belong to `generatorMode` (`dataList 0..3`) with one adjacent bit owned by
something else. It is restricted to the one model where the behaviour was observed, and should not be
extended without a fresh single-attribute sweep.

## Cross-attribute rules

Some combinations are silently rejected — the AC drops the entire group-set and stays as it was. The
known case is **fan-only combined with fan=auto**; the digital model's `constraints[]` block expresses
this (and, on the reference unit, asks for `windSpeed=3` when entering fan-only). Heat needs no such
rule, confirmed on hardware.

## Deriving a new model's layout

See [`docs/new-model.md`](docs/new-model.md). Three status captures in known states are enough to
pin the word block and identify the sensor bytes by elimination.
