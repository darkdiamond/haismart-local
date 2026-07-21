"""Per-model attribute profiles — the model-specific ``AttributeProfile`` seam. Selected by the cloud
``product_code`` / ``pid`` (e.g. ``AAC1UKZ01`` / ``PID_AAC1UKZ01``).

The AAC1UKZ01 enums are **authoritative from the device digital model** (the constraintfile the app
downloads at bind time; see :func:`profile_from_device_config`) — operationMode 0=auto/1=cool/2=dry/
6=fan_only (no heat), windSpeed 1=high/2=medium/3=low/5=auto, targetTemperature 16-30 step 1. These were
independently cross-checked against a live ``getAttributeMap`` dump + a one-attribute-at-a-time app sweep
on real units, all consistent.

No prior open-source project maps this uSDK-EPP local path (haier-esphome/smartair2 is the unrelated
``FF FF`` UART protocol); this profile is original to this project.
"""
from __future__ import annotations

from .models import AttributeProfile

# AAC1UKZ01 enums are now AUTHORITATIVE — from the device digital model (constraintfile)
# `HSU-24VRRA03TF@<uPlusId>@2.0.1.signed.json` pulled from the app (see profile_from_device_config).
# It's a cooling-only shared/rental AC (共享空调): modes auto/cool/dry/fan, no heat.
AAC1UKZ01 = AttributeProfile(
    power_attr="onOffStatus",
    mode_attr="operationMode",
    target_temp_attr="targetTemperature",
    indoor_temp_attr="indoorTemperature",
    humidity_attr="indoorHumidity",
    fan_attr="windSpeed",
    power_on_value="true",
    power_off_value="false",
    mode_values={           # numeric STD code -> normalized token (from the digital model)
        "0": "auto",        # 智能/自动/舒适
        "1": "cool",        # 制冷
        "2": "dry",         # 除湿
        "6": "fan_only",    # 送风  (no heat mode on this model)
    },
    fan_values={
        "1": "high",        # 高
        "2": "medium",      # 中
        "3": "low",         # 低
        "5": "auto",        # 自动
    },
    min_temp=16.0,
    max_temp=30.0,
    temp_step=1.0,
)

# Map the model's Chinese value descriptions -> our normalized tokens (keyword match, longest first).
_MODE_KEYWORDS = (("制冷", "cool"), ("制热", "heat"), ("除湿", "dry"), ("送风", "fan_only"),
                  ("通风", "fan_only"), ("智能", "auto"), ("自动", "auto"), ("舒适", "auto"))
_FAN_KEYWORDS = (("高", "high"), ("中", "medium"), ("低", "low"), ("自动", "auto"))


def _enum_from_datalist(data_list, keywords) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in data_list or []:
        desc = item.get("desc") or ""
        for kw, tok in keywords:
            if kw in desc:
                out[str(item.get("data"))] = tok
                break
    return out


def profile_from_device_config(config: dict) -> AttributeProfile:
    """Build an ``AttributeProfile`` from a Haier device digital-model / constraintfile JSON.

    This is the *queryable* path the user identified: the model config (fetched during device binding,
    or by ``getDeviceFuncNew?mode=<productCode>`` / the ``constraintfile`` resource) fully specifies each
    attribute's ``valueRange``, so any model self-maps instead of being hand-coded. Enum descriptions are
    Haier's (Chinese) and are matched to normalized tokens by keyword.
    """
    attrs = {a["name"]: a for a in config.get("attributes", [])}

    def datalist(name):
        return ((attrs.get(name) or {}).get("valueRange") or {}).get("dataList")

    def step_bounds(name, dflt_min, dflt_max, dflt_step):
        ds = ((attrs.get(name) or {}).get("valueRange") or {}).get("dataStep") or {}
        try:
            return (float(ds.get("minValue", dflt_min)), float(ds.get("maxValue", dflt_max)),
                    float(ds.get("step", dflt_step)))
        except (TypeError, ValueError):
            return dflt_min, dflt_max, dflt_step

    mn, mx, step = step_bounds("targetTemperature", 16.0, 30.0, 1.0)
    return AttributeProfile(
        mode_values=_enum_from_datalist(datalist("operationMode"), _MODE_KEYWORDS)
        or dict(AttributeProfile().mode_values),
        fan_values=_enum_from_datalist(datalist("windSpeed"), _FAN_KEYWORDS)
        or dict(AttributeProfile().fan_values),
        min_temp=mn, max_temp=mx, temp_step=step,
    )


# --- write validation against the device digital model (safety guard) ---------

def writable_attributes(config: dict) -> dict[str, dict]:
    """Map of attribute name -> its model spec, for attributes the model marks ``writable``."""
    return {a["name"]: a for a in config.get("attributes", []) if a.get("writable")}


def validate_write(
    config: dict, name: str, value, *, require_writable: bool = True
) -> tuple[bool, str]:
    """Gate a proposed control write against the device digital model BEFORE it is ever encoded.

    Refuses anything that isn't a writable attribute with a value the model allows — so a control
    command can never carry an unknown attribute, an out-of-range temperature, or an invalid enum.
    (The user's safety point: use the product constraints to limit the input.) LIST attrs must match a
    ``valueRange.dataList`` code; STEP attrs must be numeric, within [min,max], and on the step grid.
    Returns ``(ok, reason)``.

    ``require_writable``: when True (default) an attribute the model flags read-only is rejected. The
    HA control path passes ``False`` because Haier's cloud model misclassifies several
    **confirmed** grSetDAC fields as non-writable (e.g. ``targetTemperature``, ``rapidMode`` —
    both observed in real app writes and verified on hardware). There, writability is authorized
    by the confirmed allowlist in ``set_grsetdac_field`` instead, and this function only gates
    the **valueRange** (bounds / enum membership).
    """
    attrs = {a["name"]: a for a in config.get("attributes", [])}
    spec = attrs.get(name)
    if spec is None:
        return False, f"unknown attribute {name!r} (not in device model)"
    if require_writable and not spec.get("writable"):
        return False, f"{name!r} is not writable (read-only in the model)"
    vr = spec.get("valueRange") or {}
    sval = str(value)
    if vr.get("type") == "LIST":
        allowed = {str(x.get("data")) for x in (vr.get("dataList") or [])}
        if sval not in allowed:
            return False, f"{name}={sval!r} not in allowed {sorted(allowed)}"
        return True, "ok"
    if vr.get("type") == "STEP":
        ds = vr.get("dataStep") or {}
        try:
            v = float(value)
            lo = float(ds["minValue"])
            hi = float(ds["maxValue"])
            st = float(ds["step"])
        except (KeyError, TypeError, ValueError):
            return False, f"{name}: non-numeric value or malformed range"
        if not (lo <= v <= hi):
            return False, f"{name}={v} out of range [{lo}, {hi}]"
        if st > 0 and abs(((v - lo) / st) - round((v - lo) / st)) > 1e-6:
            return False, f"{name}={v} not on step grid (step {st} from {lo})"
        return True, "ok"
    return False, f"{name}: unsupported valueRange type {vr.get('type')!r}"

# The AC's full STD attribute set (uSDKDevice.getAttributeMap, AAC1UKZ01) — reference for the HA layer.
AAC1UKZ01_ATTRIBUTES: tuple[str, ...] = (
    "onOffStatus", "operationMode", "operationModeHK", "targetTemperature", "indoorTemperature",
    "outdoorTemperature", "indoorHumidity", "targetHumidity", "windSpeed", "windDirectionVertical",
    "windDirectionHorizontal", "tempUnit", "acType", "useMode", "opSrc", "errCode", "ErrAckFlag",
    "healthMode", "rapidMode", "silentSleepStatus", "muteStatus", "lightStatus", "screenDisplayStatus",
    "echoStatus", "lockStatus", "energySavingStatus", "energySavePeriod", "electricHeatingStatus",
    "10degreeHeatingStatus", "halfDegreeSettingStatus", "selfCleaningStatus", "selfCleaning56Status",
    "sensingResult", "humanSensingStatus", "intelligenceStatus", "pmvStatus", "specialMode",
    "generatorMode", "freshAirStatus", "humidificationStatus", "localCtrValid", "localFilterChangeFlag",
    "airQuality", "pm2p5Level", "indoorPM2p5Value", "outdoorPM2p5Value", "vocValue", "ch2oValue",
    "co2Value", "totalElectricityUsed", "totalCleaningTime",
)

# Registry keyed by cloud product_code / pid (usdk_os.db cloud_device.product_code / .pid).
PROFILES: dict[str, AttributeProfile] = {
    "AAC1UKZ01": AAC1UKZ01,
    "PID_AAC1UKZ01": AAC1UKZ01,
}


def profile_for(type_id: str | None) -> AttributeProfile:
    """Return the AttributeProfile for a product_code/pid, or a generic default if unknown."""
    if type_id and type_id in PROFILES:
        return PROFILES[type_id]
    return AttributeProfile()
