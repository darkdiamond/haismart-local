"""Diagnostics: a redacted snapshot for bug reports."""
from __future__ import annotations

from typing import Any

from haismart_hrdp import STATUS_LAYOUTS, derive_status_layout, select_wire_model
from haismart_hrdp.udiscovery import CLOUD_STATES
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLOUD_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_GATEWAY_PASSWORD,
    CONF_GATEWAY_USERNAME,
    CONF_LOCAL_KEY,
    CONF_REFRESH_TOKEN,
)
from .coordinator import HaismartConfigEntry

# Diagnostics is the artefact users are told to attach to GitHub issues, so this list has to cover
# EVERY credential in `entry.data`. It once redacted only the localKey and the deviceId while
# leaving the account tokens in the clear — and `refresh_token` is durable and reusable, so
# publishing one grants indefinite access to the whole Haier account and every AC on it.
# The deviceId stays redacted even though it is only the Wi-Fi MAC and not a credential: it is a
# stable device identifier, and nothing here needs it — the report bytes a maintainer works offsets
# out from are dumped separately under `last_raw_status` / `report`.
TO_REDACT = {
    CONF_LOCAL_KEY,
    CONF_REFRESH_TOKEN,
    CONF_ACCESS_TOKEN,
    CONF_CLOUD_CLIENT_ID,
    CONF_GATEWAY_USERNAME,
    CONF_GATEWAY_PASSWORD,
    CONF_DEVICE_ID,
    "unique_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HaismartConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    profile = coordinator.profile
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "localkey_version": coordinator.localkey_version,
        "last_update_success": coordinator.last_update_success,
        "state": coordinator.data,
        # raw report bytes (post-decrypt) — carries no secrets, invaluable for offset bugs
        "last_raw_status": (
            coordinator.last_raw_status.hex() if coordinator.last_raw_status else None
        ),
        # enum codes the device's OWN digital model authorizes for the write path — this is what
        # decides whether e.g. heat is usable on this unit (coordinator._model_authorized_codes)
        "model_authorized_codes": {
            name: sorted(codes) for name, codes in coordinator.model_codes.items()
        },
        # Everything a maintainer needs to add a layout, without a second round-trip.
        "report": {
            "length": len(coordinator.last_raw_status or b"") or None,
            "unknown_layout": coordinator.unknown_layout,
            "read_only_layout": coordinator.read_only_layout,
            "known_lengths": sorted(STATUS_LAYOUTS),
            "uplus_id": coordinator.uplus_id,
            "layout": _layout_summary(coordinator),
        },
        # Whether the AC itself can reach Haier's cloud (key-free UDISCOVERY query). Worth having
        # in a bug report: a unit that is cut off cannot be re-keyed, which explains a stale
        # localKey failure that would otherwise look like a protocol bug.
        "cloud": {
            "connected": coordinator.cloud_connected,
            "raw_state": coordinator.cloud_state,
            "state_name": CLOUD_STATES.get(coordinator.cloud_state or -1),
            "supported": coordinator.supports_udiscovery,
            # Where the AC says it is, and whether that still agrees with the address this entry
            # uses. `host_matches: false` means the unit moved on DHCP and the entry is stale --
            # worth stating outright, because it presents as "the AC stopped responding" and is
            # otherwise indistinguishable from a dead unit or a bad key.
            "reported_host": coordinator.reported_host,
            "reported_port": coordinator.reported_port,
            "host_matches": (
                None
                if coordinator.reported_host is None
                else coordinator.reported_host == coordinator.host
            ),
        },
        "digital_model": _model_summary(coordinator.digital_model),
        "profile": {
            "product_code": coordinator.product_code,
            "modes": dict(profile.mode_values),
            "fan_modes": dict(profile.fan_values),
            "min_temp": profile.min_temp,
            "max_temp": profile.max_temp,
            "temp_step": profile.temp_step,
        },
    }


def _layout_summary(coordinator) -> dict[str, Any] | None:
    """Which layout was used for the stored blob, and whether it was confirmed or derived."""
    blob = coordinator.last_raw_status
    if not blob:
        return None
    # A non-classic family decoded by the wire-model registry (e.g. compact-12) — report which one.
    if len(blob) not in STATUS_LAYOUTS:
        wm = select_wire_model(len(blob), coordinator.uplus_id)
        if wm is not None:
            return {"resolved": True, "family": wm.family, "writable": wm.writable}
    layout = derive_status_layout(blob, coordinator.digital_model)
    if layout is None:
        return {"resolved": False}
    return {
        "resolved": True,
        "verified": layout.verified,      # False == derived from the length, not a confirmed entry
        "words": layout.words,
        "indoor_temp_offset": layout.indoor_temp,
        "outdoor_temp_offset": layout.outdoor_temp,
    }


def _model_summary(model: dict[str, Any] | None) -> dict[str, Any] | None:
    """The parts of the digital model that describe CAPABILITIES.

    The full model is large and contains device-identifying ids, so only the attribute value ranges
    and the grSetDAC attribute order are included -- which is all that is needed to work out a
    layout, and carries no credential.
    """
    if not model:
        return None
    attributes = {
        a.get("name"): (a.get("valueRange") or {})
        for a in model.get("attributes", [])
        if a.get("name")
    }
    group_commands = {
        g.get("name"): g.get("attrNameList")
        for g in model.get("groupCommands", [])
        if g.get("name")
    }
    return {"attributes": attributes, "groupCommands": group_commands}
