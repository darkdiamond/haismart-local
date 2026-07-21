"""Diagnostics: a redacted snapshot for bug reports."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY
from .coordinator import HaismartConfigEntry

TO_REDACT = {CONF_LOCAL_KEY, CONF_DEVICE_ID, "unique_id"}


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
        "profile": {
            "product_code": coordinator.product_code,
            "modes": dict(profile.mode_values),
            "fan_modes": dict(profile.fan_values),
            "min_temp": profile.min_temp,
            "max_temp": profile.max_temp,
            "temp_step": profile.temp_step,
        },
    }
