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
path in ``uss.py`` untouched. This module adds *read-only* decoding for other families; writes remain
gated to the classic family until a family is capture-confirmed on real hardware (see
``uss.grsetdac_baseline_from_status``).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

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
class WireModel:
    """A read-only decoder for one AC family, selected by uPlusId or report length.

    ``fields`` maps a ``parse_full_status`` output key to its :class:`WireField`. ``mode``/``fan_mode``
    tokens are derived from ``operation_mode``/``wind_speed`` via the profile, exactly as the classic
    path does, so the coordinator/entities see the same shape.
    """

    family: str
    report_lengths: frozenset[int]
    fields: Mapping[str, WireField]
    uplus_ids: frozenset[str] = frozenset()
    writable: bool = False          # writes stay gated to capture-confirmed families
    indoor_key: str = "current_temperature"
    target_key: str = "target_temperature"

    def matches(self, length: int, uplus_id: str | None) -> bool:
        if uplus_id and uplus_id in self.uplus_ids:
            return True
        return length in self.report_lengths

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

# The "compact-12" family: a 12-word report (117 B) where every attribute — sensors included — lives
# in the word array (unlike the classic family's separate sensor block). Transcribed from APK presets
# `00000000000000008080000000041410` / `01c12002400081034080000000100000` and validated field-for-
# field against three real captured reports (haismart-local issue #4, HSU-12HFMF): power/setpoint/
# indoor/mode/fan/both swings all matched the reporter's stated state.
#
# Deliberately omitted: outdoorTemperature (word 2) — the device's own digital model does not declare
# it and the raw value reads like a condenser probe (~59 C), so publishing it would poison long-term
# statistics; and the secondary toggles — every capture had them OFF, giving no positive confirmation
# of their bit positions, so they stay off the read until a capture exercises them. Both can be added
# once evidence exists. writable=False: the group-set for this family is eppCmd 4d5f (not the classic
# 6001) and no write has been captured, so control stays disabled.
COMPACT12 = WireModel(
    family="compact12",
    report_lengths=frozenset({117}),
    writable=False,
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
