"""Temperature sensors decoded from the AC's full-status report.

Only fields the read path actually decodes become entities (this basic cooling unit reports
no humidity/air-quality hardware — those attributes read 0 in the report and are skipped).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_HOST, CONF_LOCALKEY_VERSION, CONF_PRODUCT_CODE
from .coordinator import HaismartConfigEntry, HaismartCoordinator
from .entity import HaismartEntity


@dataclass(frozen=True, kw_only=True)
class HaismartSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], float | None]


SENSORS: tuple[HaismartSensorDescription, ...] = (
    HaismartSensorDescription(
        key="current_temperature",
        translation_key="indoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.get("current_temperature"),
    ),
    HaismartSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.get("outdoor_temperature"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaismartConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    # Create every sensor unconditionally and let `native_value` return None when a reading is
    # absent.
    # Gating creation on the FIRST poll's values meant a sensor missing at setup (a failed first
    # refresh, or a report whose layout we can only partially decode) never appeared until the entry
    # was reloaded.
    entities: list[SensorEntity] = [HaismartSensor(coordinator, desc) for desc in SENSORS]
    # opt-in backup entity: exposes the localKey so it rides along in HA backups / can be copied.
    # It's a secret, so it's diagnostic + DISABLED by default (enable it, back it up, done).
    entities.append(HaismartLocalKeySensor(coordinator))
    async_add_entities(entities)


class HaismartSensor(HaismartEntity, SensorEntity):
    entity_description: HaismartSensorDescription

    def __init__(
        self, coordinator: HaismartCoordinator, description: HaismartSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class HaismartLocalKeySensor(HaismartEntity, SensorEntity):
    """The AC's current localKey, for backup/export. Diagnostic + disabled by default (a secret).

    Enable it to see/copy the key (it rides along in HA backups); the attributes carry all the
    `manual` onboarding path needs (host + deviceId + version), a one-stop cloud-independent backup.
    Stays current across localKey rotation (the coordinator updates it in place)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "local_key"
    _attr_icon = "mdi:key-variant"

    def __init__(self, coordinator: HaismartCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_local_key"

    @property
    def native_value(self) -> str:
        return self.coordinator.local_key

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        return {
            CONF_HOST: c.host,
            "device_id": c.device_id,
            CONF_LOCALKEY_VERSION: c.localkey_version,
            CONF_PRODUCT_CODE: c.product_code,
        }
