"""Per-family EPP wire models — the positional attribute map for status reports whose layout is
*not* the classic split-AC family that :func:`haismart_hrdp.uss.parse_full_status` decodes inline.

Background
----------
Every Haier AC packs its status attributes into a bit-field array of 16-bit big-endian words that
begins at byte 92 of the decrypted report (right after the ``6d 01`` getAllProperty response code).
*Where* each attribute sits in that array is the **wire model**. Haier ships one wire model per
device as a preset under the app's ``assets/com.haier.uhome.usdk/<uPlusId>`` (174 of them), and the
app resolves a device to its model by uPlusId. There is **no** cloud endpoint that serves the wire
model to us and the device *digital* model (valueRange/enums, which we do fetch) carries no
positional data — so these maps are transcribed from the APK presets and validated against real
captured reports.

What a "family" is
------------------
A family is a **distinct field map**, and one map can span several report lengths (the classic
split-AC family appears at report lengths 109/121/125 in the presets and on our own hardware at
127 — only its trailing word count differs). So report length alone is a *good but imperfect* key:
among AC split units each length maps to a single field map, but the presets do contain a genuine
collision at 149 B (a floor/heat-pump class we don't target). Selection therefore prefers an exact
uPlusId match and otherwise keys on report length **with a decode sanity-check** (see
:meth:`WireModel.decode`), degrading to the caller's unknown-layout path rather than mis-decoding.

The classic family stays in ``uss.py``
--------------------------------------
The classic 125/127-byte family keeps its existing hardware-verified decode + the grSetDAC **write**
path in ``uss.py`` untouched. This module adds decoding for other families, plus — for a family whose
group-set command is fully specified by the APK preset (``group_cmd`` + :attr:`WireModel.write_fields`)
— a **control encoder** built on that spec. That spec basis is the same one heat mode shipped on
(model-derived, method hardware-confirmed on other units); a family without a ``group_cmd`` stays
monitoring-only.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

# Attribute-array geometry, shared with uss.py (kept local to avoid an import cycle).
_ATTR_BASE = 92          # first attribute byte; word N (1-based) starts at _ATTR_BASE + 2*(N-1)
_PLAUSIBLE_INDOOR_C = (0.0, 60.0)   # a decoded indoorTemperature outside this ⇒ wrong family
_PLAUSIBLE_TARGET_C = (10.0, 40.0)  # setpoints live in ~16..30; a wide band still catches a mis-decode


@dataclass(frozen=True)
class WireField:
    """One attribute's position in the word array plus how to turn its raw bits into a value.

    ``word`` is 1-based (word 1 = bytes 92..93). ``bit`` is the LSB within the 16-bit big-endian
    word (bit 0 = least-significant). ``kind`` selects the decode:

    * ``"bool"``  -> ``bool(raw)``
    * ``"int"``   -> ``raw * k + c`` (a temperature/number)
    * ``"enum"``  -> ``enum[raw]`` — maps the raw EPP value to a **Haier STD code string** (so the
      per-model :class:`~haismart_hrdp.models.AttributeProfile` can name it), or drops the field when
      the raw value isn't in the map.
    """

    word: int
    bit: int
    length: int
    kind: str = "int"
    k: float = 1.0
    c: float = 0.0
    enum: Mapping[int, str] | None = None

    def read(self, data: bytes):
        off = _ATTR_BASE + (self.word - 1) * 2
        if off + 1 >= len(data):
            return None
        raw = ((data[off] << 8) | data[off + 1]) >> self.bit & ((1 << self.length) - 1)
        if self.kind == "bool":
            return bool(raw)
        if self.kind == "enum":
            return None if self.enum is None else self.enum.get(raw)
        return raw * self.k + self.c


@dataclass(frozen=True)
class WriteField:
    """How a control change for one attribute is packed into the group-set word array.

    The coordinator hands control values in the *classic* representation (the same one the climate
    entity builds): a Haier STD code for enums, ``°C − 16`` for the setpoint, ``0/1`` for booleans,
    and a device-specific raw value for swing. ``kind`` says how to turn that into this family's raw
    wire (EPP) value before packing at ``word``/``bit``:

    * ``"passthrough"`` — already the wire value (setpoint, on/off).
    * ``"std_enum"``   — a STD code; map via ``std_to_epp`` (refuse anything not in it).
    * ``"onoff"``      — any nonzero classic "on" value packs ``on_value``; ``0`` packs ``0`` (swing,
      whose classic raw value — 0x0c / 7 — is not this family's code).
    """

    word: int
    bit: int
    length: int
    kind: str = "passthrough"
    std_to_epp: Mapping[int, int] | None = None
    on_value: int = 1


@dataclass(frozen=True)
class WireModel:
    """A decoder (and, when ``group_cmd`` is set, a control encoder) for one AC family, selected by
    uPlusId or report length.

    ``fields`` maps a ``parse_full_status`` output key to its :class:`WireField`. ``mode``/``fan_mode``
    tokens are derived from ``operation_mode``/``wind_speed`` via the profile, exactly as the classic
    path does, so the coordinator/entities see the same shape.

    ``writable`` families additionally define ``group_cmd`` (the group-set EPP command), ``word_count``
    (the settable word array spans words 1..word_count from byte 92) and ``write_fields`` (the packing
    map). Control is a read-modify-write group-set: seed the word array from a live report, flip the
    requested fields, wrap in an ``FF FF`` frame with ``group_cmd``.
    """

    family: str
    report_lengths: frozenset[int]
    fields: Mapping[str, WireField]
    uplus_ids: frozenset[str] = frozenset()
    writable: bool = False          # False = monitoring-only (no confirmed control path)
    indoor_key: str = "current_temperature"
    target_key: str = "target_temperature"
    group_cmd: bytes | None = None  # group-set EPP command (e.g. b"\x4d\x5f")
    word_count: int = 0             # settable words 1..word_count (from byte 92)
    write_fields: Mapping[str, WriteField] = field(default_factory=dict)

    def matches(self, length: int, uplus_id: str | None) -> bool:
        if uplus_id and uplus_id in self.uplus_ids:
            return True
        return length in self.report_lengths

    def baseline_words(self, report: bytes) -> bytearray:
        """The settable word array (words 1..word_count) sliced from a full-status report — the seed
        for a group-set so untouched attributes are preserved."""
        end = _ATTR_BASE + 2 * self.word_count
        if len(report) < end:
            raise ValueError(f"report too short ({len(report)}) for {self.family} baseline")
        return bytearray(report[_ATTR_BASE:end])

    def encode_control(self, baseline: bytes, changes: Mapping[str, int]) -> bytes:
        """Pack ``changes`` ({classic field name: classic value}) into a copy of ``baseline`` (the
        settable word array). Refuses any field/value not in :attr:`write_fields` — the encoder
        safety guard: control can only ever emit a mapped attribute with a supported value."""
        if not self.group_cmd or not self.write_fields:
            raise ValueError(f"{self.family} has no confirmed control path")
        words = bytearray(baseline)
        for name, value in changes.items():
            wf = self.write_fields.get(name)
            if wf is None:
                raise KeyError(f"{name!r} is not a writable field on {self.family}")
            epp = self._to_epp(wf, name, int(value))
            off = (wf.word - 1) * 2
            if off + 1 >= len(words):
                raise ValueError(f"{name}: word {wf.word} outside the {self.family} word array")
            word = (words[off] << 8) | words[off + 1]
            mask = ((1 << wf.length) - 1) << wf.bit
            word = (word & ~mask) | ((epp << wf.bit) & mask)
            words[off], words[off + 1] = (word >> 8) & 0xFF, word & 0xFF
        return bytes(words)

    def _to_epp(self, wf: WriteField, name: str, value: int) -> int:
        if wf.kind == "std_enum":
            epp = (wf.std_to_epp or {}).get(value)
            if epp is None:
                raise ValueError(
                    f"{name}={value} is not a supported code on {self.family} "
                    f"(allowed {sorted(wf.std_to_epp or {})})"
                )
        elif wf.kind == "onoff":
            epp = wf.on_value if value else 0
        else:  # passthrough
            epp = value
        if not 0 <= epp < (1 << wf.length):
            raise ValueError(f"{name}={epp} does not fit its {wf.length}-bit field on {self.family}")
        return epp

    def decode(self, data: bytes, profile=None) -> dict | None:
        """Decode ``data`` to the ``parse_full_status`` dict, or ``None`` if the result fails the
        plausibility sanity-check (the guard that makes length-keying safe against a collision:
        a wrong family reads an implausible indoor temperature / setpoint)."""
        out: dict = {}
        for key, wf in self.fields.items():
            val = wf.read(data)
            if val is not None:
                out[key] = val

        indoor = out.get(self.indoor_key)
        if indoor is not None and not _PLAUSIBLE_INDOOR_C[0] <= indoor <= _PLAUSIBLE_INDOOR_C[1]:
            return None
        target = out.get(self.target_key)
        if target is not None and not _PLAUSIBLE_TARGET_C[0] <= target <= _PLAUSIBLE_TARGET_C[1]:
            return None

        if profile is not None:
            if "operation_mode" in out:
                out["mode"] = profile.normalized_mode(out["operation_mode"])
            if "wind_speed" in out:
                out["fan_mode"] = profile.normalized_fan(out["wind_speed"])
        # Markers the coordinator reads: a known non-classic family (NOT "unknown", so no repair is
        # raised) that is display-only until its write path is capture-confirmed.
        out["layout"] = self.family
        out["writable"] = self.writable
        return out


# --- registry ---------------------------------------------------------------------------------

# operationMode / windSpeed enums map the raw EPP index -> the Haier STD code string the digital
# model uses, so the AttributeProfile names them (STD 4 = heat, 2 = dry, 6 = fan, etc.). Provenance:
# the APK preset's own stdCode:eppValue table (`[模式]^20200D…302004:02…`), i.e. epp 2 == STD "4".
_COMPACT12_MODE = {0: "0", 1: "1", 2: "4", 3: "6", 4: "2"}   # auto / cool / heat / fan_only / dry
_COMPACT12_FAN = {0: "1", 1: "2", 2: "3", 3: "5"}            # high / medium / low / auto

# Control (group-set): the APK preset's `[组命令]` line fully specifies the group command — eppCmd
# `4d5f`, a 12-word array (words 1..12, the same span as the report), and each settable field's
# stdCode->eppValue map. This is the SAME spec basis as heat mode (issue #1): derived from the model,
# not captured on this exact family, but the group-set method is hardware-confirmed on other units.
# Control is read-modify-write from a live report, and the encoder refuses any field/value not below.
#   operationMode STD->EPP: 0->0 (auto) 1->1 (cool) 4->2 (heat) 6->3 (fan) 2->4 (dry)
#   windSpeed     STD->EPP: 1->0 (high) 2->1 (med) 3->2 (low) 5->3 (auto)
#   swings: STD 8/7 (auto) -> EPP 1, STD 0 (fixed) -> EPP 0 ("onoff": the classic 0x0c/7 "on" -> 1)
#   targetTemperature: EPP = °C - 16 (same as classic); onOffStatus: 0/1 (same as classic)
_COMPACT12_WRITE = {
    "operationMode": WriteField(6, 0, 16, "std_enum", std_to_epp={0: 0, 1: 1, 4: 2, 6: 3, 2: 4}),
    "windSpeed": WriteField(7, 0, 16, "std_enum", std_to_epp={1: 0, 2: 1, 3: 2, 5: 3}),
    "windDirectionVertical": WriteField(8, 0, 1, "onoff", on_value=1),
    "windDirectionHorizontal": WriteField(8, 1, 1, "onoff", on_value=1),
    "targetTemperature": WriteField(12, 0, 16, "passthrough"),
    "onOffStatus": WriteField(9, 0, 1, "passthrough"),
}

# The "compact-12" family: a 12-word report (117 B) where every attribute — sensors included — lives
# in the word array (unlike the classic family's separate sensor block). Transcribed from APK presets
# `00000000000000008080000000041410` / `01c12002400081034080000000100000` and validated field-for-
# field against three real captured reports (haismart-local issue #4, HSU-12HFMF): power/setpoint/
# indoor/mode/fan/both swings all matched the reporter's stated state.
#
# Deliberately omitted from the READ: outdoorTemperature (word 2) — the device's own digital model
# does not declare it and the raw value reads like a condenser probe (~59 C), so publishing it would
# poison long-term statistics; and the secondary toggles — every capture had them OFF, giving no
# positive confirmation of their bit positions, so they stay off the read until a capture exercises
# them. Both can be added once evidence exists. Control covers the core climate fields (power / mode /
# fan / setpoint / both swings) via the APK-specified group command.
COMPACT12 = WireModel(
    family="compact12",
    report_lengths=frozenset({117}),
    writable=True,
    group_cmd=b"\x4d\x5f",
    word_count=12,
    write_fields=_COMPACT12_WRITE,
    fields={
        "power": WireField(9, 0, 1, kind="bool"),
        "target_temperature": WireField(12, 0, 16, kind="int", k=1.0, c=16.0),
        "current_temperature": WireField(1, 0, 16, kind="int", k=1.0, c=0.0),
        "operation_mode": WireField(6, 0, 16, kind="enum", enum=_COMPACT12_MODE),
        "wind_speed": WireField(7, 0, 16, kind="enum", enum=_COMPACT12_FAN),
        "swing_vertical": WireField(8, 0, 1, kind="bool"),
        "swing_horizontal": WireField(8, 1, 1, kind="bool"),
    },
)

# Every non-classic family known to the library. The classic 125/127 family is NOT here — it keeps
# its verified inline decode + write path in uss.py.
WIRE_MODELS: tuple[WireModel, ...] = (COMPACT12,)


def select_wire_model(length: int, uplus_id: str | None = None) -> WireModel | None:
    """The :class:`WireModel` for a report, preferring an exact uPlusId match and otherwise keying on
    report ``length``. Returns ``None`` when nothing matches, or when the length is ambiguous across
    families and no uPlusId disambiguates it (safer to fall back to the unknown-layout path than to
    guess). The caller must still gate on the classic lengths owning their inline decode."""
    if uplus_id:
        for wm in WIRE_MODELS:
            if uplus_id in wm.uplus_ids:
                return wm
    candidates = [wm for wm in WIRE_MODELS if length in wm.report_lengths]
    return candidates[0] if len(candidates) == 1 else None
