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
    entities: list[BinarySensorEntity] = [
        HaismartBinarySensor(coordinator, desc) for desc in BINARY_SENSORS
    ]
    entities.append(HaismartCloudConnectionSensor(coordinator))
    async_add_entities(entities)


class HaismartCloudConnectionSensor(HaismartEntity, BinarySensorEntity):
    """Whether the AC itself can currently reach Haier's cloud.

    Answered by the appliance over the key-free UDISCOVERY query on UDP :7083 -- no account, no
    localKey, and no request to Haier -- which is what makes it usable as verification for someone
    who has deliberately firewalled the unit. `on` means the AC is talking to the cloud; `off` means
    it is cut off, which for that user is the desired state.

    Note the asymmetric latency: losing the cloud takes about four minutes to appear (the module has
    to time out a keepalive first), regaining it under twenty seconds. `None` -- unknown -- when the
    unit does not answer the query at all, never a fabricated `off`.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "cloud_connection"

    def __init__(self, coordinator: HaismartCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_cloud_connection"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.cloud_connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # The raw code is worth keeping: only 1000 (connected) and 1006 (cut off) have been
        # observed, so anything else is a datapoint worth reporting rather than flattening away.
        return {"raw_state": self.coordinator.cloud_state}


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
