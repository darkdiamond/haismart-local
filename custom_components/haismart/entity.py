"""Base entity wiring device info + coordinator for Haismart entities."""
from __future__ import annotations

import re

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import HaismartCoordinator

_MAC_ID = re.compile(r"^[0-9A-Fa-f]{12}$")


class HaismartEntity(CoordinatorEntity[HaismartCoordinator]):
    """Common base: attaches every entity to one HA device per AC."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HaismartCoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device_id
        # on this hardware the uSDK deviceId IS the wifi module's MAC (e.g. A1B2C3D4E5F6)
        connections = (
            {(CONNECTION_NETWORK_MAC, format_mac(device_id))}
            if _MAC_ID.match(device_id)
            else set()
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            connections=connections,
            manufacturer=MANUFACTURER,
            model=coordinator.product_code,
            name=coordinator.config_entry.title,
        )
