"""Tests for the AAC1UKZ01 attribute profile (from the device digital model + app-UI correlation)."""
import json
import pathlib

from haismart_hrdp import AttributeProfile, profile_for, profile_from_device_config
from haismart_hrdp.profiles import AAC1UKZ01, AAC1UKZ01_ATTRIBUTES

_DEVCONFIG = pathlib.Path(__file__).parent / "fixtures" / "AAC1UKZ01_devconfig.json"


def test_profile_lookup_by_product_code():
    assert profile_for("AAC1UKZ01") is AAC1UKZ01
    assert profile_for("PID_AAC1UKZ01") is AAC1UKZ01
    # unknown -> generic default, never a crash
    assert isinstance(profile_for("SOMETHING_ELSE"), AttributeProfile)
    assert isinstance(profile_for(None), AttributeProfile)


def test_mode_enum_confirmed_values():
    # CONFIRMED against the app UI on real hardware
    assert AAC1UKZ01.normalized_mode("1") == "cool"
    assert AAC1UKZ01.normalized_mode("6") == "fan_only"
    assert AAC1UKZ01.std_mode("cool") == "1"
    assert AAC1UKZ01.std_mode("fan_only") == "6"


def test_fan_enum_confirmed_values():
    assert AAC1UKZ01.normalized_fan("2") == "medium"
    assert AAC1UKZ01.normalized_fan("3") == "low"
    assert AAC1UKZ01.std_fan("low") == "3"


def test_power_and_attr_names():
    assert AAC1UKZ01.power_attr == "onOffStatus"
    assert AAC1UKZ01.power_on_value == "true" and AAC1UKZ01.power_off_value == "false"
    assert AAC1UKZ01.target_temp_attr == "targetTemperature"
    assert AAC1UKZ01.fan_attr == "windSpeed"
    assert AAC1UKZ01.mode_attr == "operationMode"


def test_full_attribute_list_present():
    for core in ("onOffStatus", "operationMode", "targetTemperature", "indoorTemperature", "windSpeed"):
        assert core in AAC1UKZ01_ATTRIBUTES
    assert len(AAC1UKZ01_ATTRIBUTES) > 40  # the AC exposes a large STD attribute set


def test_validate_write_against_model():
    from haismart_hrdp import validate_write, writable_attributes
    cfg = json.loads(_DEVCONFIG.read_text())
    # valid writes
    assert validate_write(cfg, "operationMode", "1")[0] is True   # cool (enum member)
    assert validate_write(cfg, "operationMode", 6)[0] is True      # fan (int coerced)
    assert validate_write(cfg, "targetTemperature", 24)[0] is True
    assert validate_write(cfg, "targetTemperature", "16")[0] is True
    # rejected writes — the safety guard
    assert validate_write(cfg, "operationMode", "4")[0] is False   # heat: not on this model
    assert validate_write(cfg, "targetTemperature", 40)[0] is False  # out of range
    assert validate_write(cfg, "targetTemperature", 24.5)[0] is False  # off the step grid (step 1)
    assert validate_write(cfg, "indoorTemperature", 20)[0] is False  # read-only sensor
    assert validate_write(cfg, "notARealAttribute", "1")[0] is False
    # writable set excludes read-only sensors
    w = writable_attributes(cfg)
    assert "operationMode" in w and "targetTemperature" in w
    assert "indoorTemperature" not in w and "outdoorTemperature" not in w


def test_profile_from_real_device_config():
    # the queryable digital model (constraintfile) should self-derive the same authoritative enums
    config = json.loads(_DEVCONFIG.read_text())
    p = profile_from_device_config(config)
    assert p.mode_values == {"0": "auto", "1": "cool", "2": "dry", "6": "fan_only"}  # no heat
    assert p.fan_values == {"1": "high", "2": "medium", "3": "low", "5": "auto"}
    assert (p.min_temp, p.max_temp, p.temp_step) == (16.0, 30.0, 1.0)
    # matches the hand-verified hardcoded profile
    assert p.mode_values == AAC1UKZ01.mode_values and p.fan_values == AAC1UKZ01.fan_values
