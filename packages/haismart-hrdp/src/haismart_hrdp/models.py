"""Core data types for the haismart-hrdp library.

The one live model type is :class:`AttributeProfile` — the per-model "cool vs 1 vs COOL" knowledge
that maps a device's STD enum values to the normalized tokens the library exposes. It is
defaults are overridable
per model, so real digital-model data slots in as configuration rather than code edits.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AttributeProfile:
    """Per-model STD attribute names + enum maps.

    This is the single home for the "cool vs 1 vs COOL" knowledge. STD enum values map to a small
    set of normalized tokens the library exposes; the HA integration maps those to HA climate.
    """

    power_attr: str = "onOffStatus"
    mode_attr: str = "operationMode"
    target_temp_attr: str = "targetTemperature"
    indoor_temp_attr: str = "indoorTemperature"
    humidity_attr: str = "indoorHumidity"
    fan_attr: str = "windSpeed"

    power_on_value: str = "true"
    power_off_value: str = "false"

    # STD value -> normalized token
    mode_values: Mapping[str, str] = field(
        default_factory=lambda: {
            "cool": "cool",
            "heat": "heat",
            "dehumidify": "dry",
            "wind": "fan_only",
            "auto": "auto",
        }
    )
    fan_values: Mapping[str, str] = field(
        default_factory=lambda: {
            "auto": "auto",
            "low": "low",
            "middle": "medium",
            "high": "high",
        }
    )

    min_temp: float = 16.0
    max_temp: float = 30.0
    temp_step: float = 1.0

    def normalized_mode(self, std_value: str | None) -> str | None:
        if std_value is None:
            return None
        return self.mode_values.get(std_value)

    def std_mode(self, normalized: str) -> str | None:
        for std, norm in self.mode_values.items():
            if norm == normalized:
                return std
        return None

    def normalized_fan(self, std_value: str | None) -> str | None:
        if std_value is None:
            return None
        return self.fan_values.get(std_value)

    def std_fan(self, normalized: str) -> str | None:
        for std, norm in self.fan_values.items():
            if norm == normalized:
                return std
        return None
