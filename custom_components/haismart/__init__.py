"""The Haismart local integration — fully-local uSS control of Haier ACs (no cloud, no MQTT)."""
from __future__ import annotations

# HACS/vendored build: bundled helper libs live in ./vendor (no pip step needed). This runs
# before any submodule import, so their top-level `from haismart_hrdp import ...` resolve.
# ruff: noqa: E402 - the sys.path shim below must precede the submodule imports by design.
import os as _os
import sys as _sys

_vendor = _os.path.join(_os.path.dirname(__file__), "vendor")
if _vendor not in _sys.path:
    _sys.path.insert(0, _vendor)

from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import HaismartConfigEntry, HaismartCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: HaismartConfigEntry) -> bool:
    coordinator = HaismartCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # A successful first read means the stored key works, so clear any stale-localKey repair left
    # over from a rotation (e.g. after a manual reauth, which reloads the entry).
    coordinator.clear_stale_localkey_issue()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: HaismartConfigEntry) -> None:
    """Reload on options change (poll interval)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HaismartConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
