"""Select entity for the AC's multi-level ECO control.

This unit's ECO is a 3-bit field (word4 b3-5) with values {0=off, 5, 6, 7} — confirmed for this
unit. It is NOT the digital model's energySavingStatus bool. The library refuses any
code outside {0,5,6,7}. NB the level<->code ordering (which of 5/6/7 the app shows as level 1/2/3)
is a display label still to be confirmed; the codes themselves are all real device states.
"""
from __future__ import annotations

from haismart_hrdp import GRSETDAC_ENUMS
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HaismartConfigEntry, HaismartCoordinator
from .entity import HaismartEntity

_ECO = GRSETDAC_ENUMS["ecoMode"]              # token -> raw EPP code (off/level1/level2/level3)
_ECO_REVERSE = {v: k for k, v in _ECO.items()}  # raw EPP code -> token


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaismartConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([HaismartEcoSelect(entry.runtime_data)])


class HaismartEcoSelect(HaismartEntity, SelectEntity):
    _attr_name = "Eco"
    _attr_icon = "mdi:leaf-circle"
    _attr_options = list(_ECO)

    def __init__(self, coordinator: HaismartCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_eco"

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.current_field("ecoMode")
        return None if value is None else _ECO_REVERSE.get(value)

    async def async_select_option(self, option: str) -> None:
        code = _ECO.get(option)
        if code is None:
            raise ValueError(f"unknown eco option {option!r}")
        await self.coordinator.async_send_control({"ecoMode": code})
