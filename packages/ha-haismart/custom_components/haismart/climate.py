"""Climate entity — generated from the per-model AttributeProfile, with local control.

Control is the grSetDAC group-set write path. Every command is seeded from the latest full-status
report so it preserves all other attributes, and the library refuses any field/value not in its
allowlist.
"""
from __future__ import annotations

from typing import Any

from haismart_hrdp import GRSETDAC_ENUMS
from homeassistant.components.climate import (
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_VERTICAL,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HaismartConfigEntry, HaismartCoordinator
from .entity import HaismartEntity

# normalized profile token <-> HA HVACMode (power/off handled separately)
_MODE_TO_HVAC = {
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "dry": HVACMode.DRY,
    "fan_only": HVACMode.FAN_ONLY,
    "auto": HVACMode.AUTO,
}
_HVAC_TO_MODE = {v: k for k, v in _MODE_TO_HVAC.items()}

# Fan-only mode on this unit won't accept fan=auto; when entering it (or if the user picks auto
# while in it) we fall back to this concrete speed. "medium" is a neutral default airflow.
_FAN_ONLY_DEFAULT_SPEED = "medium"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaismartConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([HaismartClimate(entry.runtime_data)])


class HaismartClimate(HaismartEntity, ClimateEntity):
    _attr_name = None  # the device name is the entity name
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    # The two axes are independent fields on the wire (vertical = word1 low nibble, horizontal =
    # word4 bits 0-2), but they are presented as ONE control with the conventional four-way choice,
    # matching how other AC integrations expose swing.
    _attr_swing_modes = [SWING_OFF, SWING_VERTICAL, SWING_HORIZONTAL, SWING_BOTH]
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: HaismartCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.device_id
        profile = coordinator.profile
        # dict order of the profile's STD maps is the model's own enum order
        seen: list[HVACMode] = [HVACMode.OFF]
        for token in profile.mode_values.values():
            hvac = _MODE_TO_HVAC.get(token)
            if hvac is not None and hvac not in seen:
                seen.append(hvac)
        self._attr_hvac_modes = seen
        fans: list[str] = []
        for token in profile.fan_values.values():
            if token not in fans:
                fans.append(token)
        self._attr_fan_modes = fans
        self._attr_min_temp = profile.min_temp
        self._attr_max_temp = profile.max_temp
        self._attr_target_temperature_step = profile.temp_step

    @property
    def _state(self) -> dict[str, Any]:
        """The last decoded status, or an empty dict before the first successful read."""
        return self.coordinator.data or {}

    @property
    def hvac_mode(self) -> HVACMode | None:
        state = self._state
        if not state:
            return None
        if state.get("power") is False:
            return HVACMode.OFF
        return _MODE_TO_HVAC.get(state.get("mode"))

    @property
    def current_temperature(self) -> float | None:
        return self._state.get("current_temperature")

    @property
    def target_temperature(self) -> float | None:
        return self._state.get("target_temperature")

    @property
    def fan_mode(self) -> str | None:
        return self._state.get("fan_mode")

    @property
    def swing_mode(self) -> str | None:
        vertical = self._state.get("swing_vertical")
        horizontal = self._state.get("swing_horizontal")
        if vertical is None and horizontal is None:
            return None
        if vertical and horizontal:
            return SWING_BOTH
        if vertical:
            return SWING_VERTICAL
        if horizontal:
            return SWING_HORIZONTAL
        return SWING_OFF

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_send_control({"onOffStatus": 0})
            return
        token = _HVAC_TO_MODE.get(hvac_mode)
        mode_val = GRSETDAC_ENUMS["operationMode"].get(token) if token else None
        if mode_val is None:
            raise ValueError(f"unsupported hvac_mode {hvac_mode}")
        # turning on and selecting the mode in one group-set
        changes: dict[str, int] = {"onOffStatus": 1, "operationMode": mode_val}
        # This unit SILENTLY REJECTS fan-only mode combined with fan=auto (verified on hardware: the
        # whole group-set is dropped and the unit stays on the previous mode). Fan-only needs a
        # concrete speed, so substitute one when the current fan is auto/unknown. The digital model
        # doesn't express this cross-attribute rule — it's observed device behaviour.
        if hvac_mode == HVACMode.FAN_ONLY and self.fan_mode in (None, "auto"):
            changes["windSpeed"] = GRSETDAC_ENUMS["windSpeed"][_FAN_ONLY_DEFAULT_SPEED]
        await self.coordinator.async_send_control(changes)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get("temperature")
        if temp is None:
            return
        await self.coordinator.async_send_control(
            {"targetTemperature": int(round(temp)) - 16}
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        # In fan-only mode the unit rejects fan=auto (see async_set_hvac_mode), so coerce it to a
        # concrete speed rather than send a write the AC will silently drop.
        if fan_mode == "auto" and self.hvac_mode == HVACMode.FAN_ONLY:
            fan_mode = _FAN_ONLY_DEFAULT_SPEED
        fan_val = GRSETDAC_ENUMS["windSpeed"].get(fan_mode)
        if fan_val is None:
            raise ValueError(f"unsupported fan_mode {fan_mode}")
        await self.coordinator.async_send_control({"windSpeed": fan_val})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        # Both axes travel in ONE grSetDAC group-set, so off -> both can never land as a
        # half-applied state, and picking one axis explicitly turns the other off.
        vertical = swing_mode in (SWING_VERTICAL, SWING_BOTH)
        horizontal = swing_mode in (SWING_HORIZONTAL, SWING_BOTH)
        v_enum = GRSETDAC_ENUMS["windDirectionVertical"]
        h_enum = GRSETDAC_ENUMS["windDirectionHorizontal"]
        await self.coordinator.async_send_control(
            {
                "windDirectionVertical": v_enum["on" if vertical else "off"],
                "windDirectionHorizontal": h_enum["on" if horizontal else "off"],
            }
        )

    async def async_turn_on(self) -> None:
        await self.coordinator.async_send_control({"onOffStatus": 1})

    async def async_turn_off(self) -> None:
        await self.coordinator.async_send_control({"onOffStatus": 0})
