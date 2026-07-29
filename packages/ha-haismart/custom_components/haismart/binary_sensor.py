"""Running-state binary sensors from the AC's extended report.

Present only on units that report the extended figures; on the rest they stay unavailable rather
than showing a made-up "off". Names live in strings.json via `translation_key`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HaismartConfigEntry, HaismartCoordinator
from .entity import HaismartEntity


@dataclass(frozen=True, kw_only=True)
class HaismartBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_SENSORS: tuple[HaismartBinarySensorDescription, ...] = (
    HaismartBinarySensorDescription(
        key="compressor_running",
        translation_key="compressor",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.get("compressor_running"),
    ),
    HaismartBinarySensorDescription(
        key="fan_running",
        translation_key="fan",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.get("fan_running"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaismartConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        HaismartBinarySensor(coordinator, desc) for desc in BINARY_SENSORS
    )


class HaismartBinarySensor(HaismartEntity, BinarySensorEntity):
    entity_description: HaismartBinarySensorDescription

    def __init__(
        self, coordinator: HaismartCoordinator, description: HaismartBinarySensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
