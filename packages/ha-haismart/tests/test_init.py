"""Entry setup, coordinator read cycle, entity state, and localKey-rotation reauth."""
from __future__ import annotations

from datetime import timedelta

from conftest import make_status_frame
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.haismart.const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_LOCAL_KEY,
    CONF_LOCALKEY_VERSION,
    CONF_PRODUCT_CODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

CLIMATE = "climate.downstairs_ac"


def _entry(**overrides) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Downstairs AC",
        unique_id="A1B2C3D4E5F6",
        data={
            CONF_HOST: "192.168.1.50",
            CONF_DEVICE_ID: "A1B2C3D4E5F6",
            CONF_LOCAL_KEY: "00112233445566778899aabbccddeeff",
            CONF_PRODUCT_CODE: "AAC1UKZ01",
            CONF_LOCALKEY_VERSION: 4,
            **overrides,
        },
    )


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _tick(hass: HomeAssistant, freezer) -> None:
    freezer.tick(timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_setup_creates_entities_from_status(hass: HomeAssistant, mock_uss) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    climate = hass.states.get(CLIMATE)
    assert climate is not None
    assert climate.state == "cool"
    assert climate.attributes["current_temperature"] == 26.5
    assert climate.attributes["temperature"] == 24.0
    assert climate.attributes["fan_mode"] == "auto"
    assert climate.attributes["swing_mode"] == "vertical"
    # both axes are independent fields on the wire but are presented as ONE conventional control
    assert climate.attributes["swing_modes"] == ["off", "vertical", "horizontal", "both"]
    assert climate.attributes["min_temp"] == 16.0
    assert climate.attributes["max_temp"] == 30.0
    assert climate.attributes["fan_modes"] == ["high", "medium", "low", "auto"]
    # OFF + the model's own enum order (cooling-only unit: no HEAT)
    assert climate.attributes["hvac_modes"] == ["off", "auto", "cool", "dry", "fan_only"]

    indoor = hass.states.get("sensor.downstairs_ac_indoor_temperature")
    outdoor = hass.states.get("sensor.downstairs_ac_outdoor_temperature")
    assert indoor is not None and float(indoor.state) == 26.5
    assert outdoor is not None and float(outdoor.state) == 33.0


async def test_powered_off_reports_hvac_off(hass: HomeAssistant, mock_uss) -> None:
    mock_uss.read.return_value = [make_status_frame(power=False)]
    await _setup(hass)
    assert hass.states.get(CLIMATE).state == "off"


def _sent_field(send, name: str) -> int:
    """Decode a grSetDAC field out of the EPP frame the coordinator sent to async_send_op."""
    from haismart_hrdp import GRSETDAC_FIELDS

    frame = send.last_frame  # the grSetDAC frame build_frame produced (see conftest)
    assert frame[:2] == b"\xff\xff" and frame[10:12] == b"\x60\x01"  # a grSetDAC frame
    words = frame[12:-1]
    wi, shift, width = GRSETDAC_FIELDS[name]
    off = (wi - 1) * 2
    word = (words[off] << 8) | words[off + 1]
    return (word >> shift) & ((1 << width) - 1)


async def test_set_temperature_sends_grsetdac(hass: HomeAssistant, mock_uss) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": CLIMATE, "temperature": 22}, blocking=True
    )
    assert mock_uss.send.await_count == 1
    assert _sent_field(mock_uss.send, "targetTemperature") == 6  # 22 - 16
    # a group-set: every other field is preserved from current state (baseline 24/cool/auto/on)
    assert _sent_field(mock_uss.send, "operationMode") == 1
    assert _sent_field(mock_uss.send, "windSpeed") == 5
    assert _sent_field(mock_uss.send, "onOffStatus") == 1


async def test_set_hvac_mode_off_then_dry(hass: HomeAssistant, mock_uss) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": CLIMATE, "hvac_mode": "off"}, blocking=True
    )
    assert _sent_field(mock_uss.send, "onOffStatus") == 0
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": CLIMATE, "hvac_mode": "dry"}, blocking=True
    )
    assert _sent_field(mock_uss.send, "onOffStatus") == 1
    assert _sent_field(mock_uss.send, "operationMode") == 2  # dry


async def test_set_fan_mode_sends(hass: HomeAssistant, mock_uss) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "climate", "set_fan_mode", {"entity_id": CLIMATE, "fan_mode": "high"}, blocking=True
    )
    assert _sent_field(mock_uss.send, "windSpeed") == 1


async def test_fan_only_mode_substitutes_concrete_fan(hass: HomeAssistant, mock_uss) -> None:
    """Regression: fan-only mode rejects fan=auto on this unit (the group-set is silently dropped).
    Entering fan-only while the fan is on auto must also send a concrete windSpeed,
    or the mode change does nothing."""
    await _setup(hass)  # baseline: cool, fan auto (make_status_frame default fan_code=5)
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": CLIMATE, "hvac_mode": "fan_only"}, blocking=True
    )
    assert _sent_field(mock_uss.send, "operationMode") == 6   # fan_only
    assert _sent_field(mock_uss.send, "onOffStatus") == 1
    assert _sent_field(mock_uss.send, "windSpeed") == 2       # medium substituted, NOT auto(5)


async def test_set_swing_mode_sends_toggle(hass: HomeAssistant, mock_uss) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "climate", "set_swing_mode", {"entity_id": CLIMATE, "swing_mode": "off"}, blocking=True
    )
    assert _sent_field(mock_uss.send, "windDirectionVertical") == 0
    await hass.services.async_call(
        "climate", "set_swing_mode", {"entity_id": CLIMATE, "swing_mode": "vertical"}, blocking=True
    )
    assert _sent_field(mock_uss.send, "windDirectionVertical") == 0x0C


async def test_switch_toggles_confirmed_bit(hass: HomeAssistant, mock_uss) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.downstairs_ac_sleep"}, blocking=True
    )
    assert _sent_field(mock_uss.send, "silentSleepStatus") == 1
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.downstairs_ac_strong"}, blocking=True
    )
    assert _sent_field(mock_uss.send, "rapidMode") == 0


async def test_eco_select_sends_level(hass: HomeAssistant, mock_uss) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.downstairs_ac_eco", "option": "level2"}, blocking=True,
    )
    assert _sent_field(mock_uss.send, "ecoMode") == 6  # level2 -> code 6


async def test_control_confirms_from_op_reply_without_extra_read(
    hass: HomeAssistant, mock_uss
) -> None:
    """The AC echoes its updated state on the op connection, so the coordinator confirms from that
    reply directly — the entity updates immediately and NO extra read cycle is issued (the group-set
    is seeded from the op's own in-session status push, not a separate read)."""
    await _setup(hass)
    reads_after_setup = mock_uss.read.await_count
    # the op reply carries the AC's updated full-status report (target now 26)
    mock_uss.send.return_value = [make_status_frame(target_temp=26)]
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": CLIMATE, "temperature": 26}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes["temperature"] == 26.0   # from the op reply
    assert mock_uss.read.await_count == reads_after_setup               # no separate read at all


async def test_control_seeds_from_in_session_push_not_stale_cache(
    hass: HomeAssistant, mock_uss
) -> None:
    """Regression (the 'setpoint won't stick' bug): a control group-set must be seeded from the AC's
    live in-session status push, not the cached ``last_raw_status``. Here the cache is stale
    (unit OFF) while the AC's push says it's really ON; a temp change must carry the fresh power
    bit, or it
    would silently turn the unit off. Assert the built op frame equals the fresh-seeded one."""
    from haismart_hrdp import uss

    entry = await _setup(hass)
    coordinator = entry.runtime_data
    stale = make_status_frame(power=False, target_temp=30)   # stale cache: OFF
    fresh = make_status_frame(power=True, target_temp=24)    # what the AC pushes in-session: ON
    coordinator.last_raw_status = stale
    mock_uss.send.baseline = fresh                            # the op-connection status push
    mock_uss.send.return_value = [make_status_frame(power=True, target_temp=25)]

    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": CLIMATE, "temperature": 25}, blocking=True
    )
    await hass.async_block_till_done()

    sent_frame = mock_uss.send.last_frame
    fresh_seeded = uss.grsetdac_op_frame(
        uss.set_grsetdac_field(
            uss.grsetdac_baseline_from_status(fresh), "targetTemperature", 25 - 16
        )
    )
    stale_seeded = uss.grsetdac_op_frame(
        uss.set_grsetdac_field(
            uss.grsetdac_baseline_from_status(stale), "targetTemperature", 25 - 16
        )
    )
    assert sent_frame == fresh_seeded  # seeded from the live in-session push (power ON preserved)
    assert sent_frame != stale_seeded       # NOT from the stale OFF cache


async def test_control_falls_back_to_read_when_reply_has_no_status(
    hass: HomeAssistant, mock_uss
) -> None:
    """If the op reply carries no decodable full-status report, confirm with a normal read cycle."""
    await _setup(hass)
    reads_after_setup = mock_uss.read.await_count
    mock_uss.send.return_value = []                                # nothing usable in the reply
    mock_uss.read.return_value = [make_status_frame(target_temp=26)]
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": CLIMATE, "temperature": 26}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes["temperature"] == 26.0
    # one read: the post-op fallback confirmation cycle (the baseline came from the op's own push)
    assert mock_uss.read.await_count == reads_after_setup + 1


async def test_setup_retries_when_unreachable(hass: HomeAssistant, mock_uss) -> None:
    mock_uss.read.side_effect = OSError("connection refused")
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_transient_outage_marks_unavailable_then_recovers(
    hass: HomeAssistant, mock_uss, freezer
) -> None:
    await _setup(hass)
    assert hass.states.get(CLIMATE).state == "cool"

    mock_uss.read.side_effect = OSError("host down")
    await _tick(hass, freezer)
    assert hass.states.get(CLIMATE).state == "unavailable"

    mock_uss.read.side_effect = None
    await _tick(hass, freezer)
    assert hass.states.get(CLIMATE).state == "cool"


async def test_localkey_rotation_triggers_reauth(
    hass: HomeAssistant, mock_uss, freezer
) -> None:
    """Empty read cycles + a newer key version on the AC -> ConfigEntryAuthFailed -> reauth."""
    entry = await _setup(hass)

    mock_uss.read.return_value = []
    mock_uss.read.side_effect = None
    mock_uss.probe.return_value = 5  # AC rotated: stored v4, AC says v5
    await _tick(hass, freezer)  # miss 1 — no probe yet
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    await _tick(hass, freezer)  # miss 2 — probe, mismatch, reauth
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    # the entry stays loaded while reauth is pending; entities go unavailable
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(CLIMATE).state == "unavailable"
    # and an actionable repair is raised advising cloud creds for auto-healing
    from homeassistant.helpers import issue_registry as ir

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, "stale_localkey_manual_reauth_A1B2C3D4E5F6"
    )
    assert issue is not None and issue.severity is ir.IssueSeverity.WARNING


def _gateway_entry() -> MockConfigEntry:
    """An entry configured for cloud MQTT-gateway localKey auto-refresh."""
    return _entry(
        gateway_username="0172114171",
        gateway_password="deadbeefdeadbeefdeadbeefdeadbeef",
        cloud_client_id="A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
        access_token="tok-abc",  # no refresh_token -> uses this token directly
    )


async def test_localkey_rotation_auto_refreshes_via_gateway(
    hass: HomeAssistant, mock_uss, freezer
) -> None:
    """With gateway creds, a rotation is healed in-place via the cloud gateway — NO reauth flow."""
    from unittest.mock import patch

    from haismart_extractor import LocalKey

    from custom_components.haismart.const import CONF_LOCALKEY_VERSION as VER

    new_key = "ffeeddccbbaa99887766554433221100"
    entry = _gateway_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    mock_uss.read.return_value = []
    mock_uss.read.side_effect = None
    mock_uss.probe.return_value = 5  # AC rotated v4 -> v5

    def _refresh(_creds, _device_id, **_kw):
        # the fresh key now decrypts the AC's status again
        mock_uss.read.return_value = [mock_uss.frame]
        return LocalKey(key=new_key, version=5)

    with patch(
        "custom_components.haismart.coordinator.get_localkey_via_gateway",
        side_effect=_refresh,
    ) as gw:
        await _tick(hass, freezer)  # miss 1
        await _tick(hass, freezer)  # miss 2 -> probe, rotation, gateway auto-refresh
        await hass.async_block_till_done()
        await _tick(hass, freezer)  # clean cycle now reads with the new key
        await hass.async_block_till_done()

    assert gw.called
    # the fetch was asked to refresh this device's key
    assert gw.call_args.args[1] == "A1B2C3D4E5F6"
    # no reauth flow; the key + version are updated in place and persisted
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert entry.data[CONF_LOCAL_KEY] == new_key
    assert entry.data[VER] == 5
    # the AC recovered on the new key (not stuck unavailable / reauth)
    assert hass.states.get(CLIMATE).state != "unavailable"
    # self-healed silently — no manual-re-key repair is raised
    from homeassistant.helpers import issue_registry as ir

    assert ir.async_get(hass).async_get_issue(
        DOMAIN, "stale_localkey_manual_reauth_A1B2C3D4E5F6"
    ) is None


async def test_gateway_refresh_failure_falls_back_to_reauth(
    hass: HomeAssistant, mock_uss, freezer
) -> None:
    """If the gateway fetch fails, the coordinator still reauths (no silent stall)."""
    from unittest.mock import patch

    from haismart_extractor import GatewayError

    entry = _gateway_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    mock_uss.read.return_value = []
    mock_uss.read.side_effect = None
    mock_uss.probe.return_value = 5
    with patch(
        "custom_components.haismart.coordinator.get_localkey_via_gateway",
        side_effect=GatewayError("CONNACK rc=4"),
    ):
        await _tick(hass, freezer)
        await _tick(hass, freezer)
        await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert entry.data[CONF_LOCAL_KEY] == "00112233445566778899aabbccddeeff"  # unchanged


async def test_diagnostics_redacts_secrets(hass: HomeAssistant, mock_uss) -> None:
    from custom_components.haismart.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["entry"][CONF_LOCAL_KEY] == "**REDACTED**"
    # The deviceId is NOT redacted: it is just the Wi-Fi MAC, it is not a credential, and it is
    # needed to interpret a status capture. What must never appear is the account credentials -
    # `refresh_token` in particular is durable and reusable, so leaking it in the file users are
    # told to attach to issues would hand over the whole Haier account.
    assert diag["entry"][CONF_DEVICE_ID] == "A1B2C3D4E5F6"
    assert diag["localkey_version"] == 4
    assert diag["state"]["mode"] == "cool"
    assert diag["profile"]["product_code"] == "AAC1UKZ01"
    # decrypted status bytes carry no secret and are kept for offset debugging
    assert diag["last_raw_status"] == mock_uss.frame.hex()


async def test_empty_reads_same_version_is_transient(
    hass: HomeAssistant, mock_uss, freezer
) -> None:
    """No decodable status but the key version still matches -> UpdateFailed, no reauth."""
    await _setup(hass)

    mock_uss.read.return_value = []
    await _tick(hass, freezer)
    await _tick(hass, freezer)  # probe runs, versions match (both v4)
    assert hass.states.get(CLIMATE).state == "unavailable"
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_prolonged_outage_never_falsely_reauths_and_throttles_probe(
    hass: HomeAssistant, mock_uss, freezer
) -> None:
    """Many empty cycles with a matching key version: never reauth, and the version probe is
    throttled (reset after a matching probe) rather than fired every single cycle."""
    await _setup(hass)
    mock_uss.read.return_value = []
    mock_uss.probe.reset_mock()

    for _ in range(6):
        await _tick(hass, freezer)

    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert hass.states.get(CLIMATE).state == "unavailable"
    # 6 empty cycles, probe every _MISSES_BEFORE_PROBE(=2) -> 3 probes, not 6
    assert mock_uss.probe.call_count == 3


async def test_recovery_after_probe_reset(hass: HomeAssistant, mock_uss, freezer) -> None:
    """A miss that reset the counter still recovers cleanly on the next good read."""
    await _setup(hass)
    mock_uss.read.return_value = []
    await _tick(hass, freezer)
    await _tick(hass, freezer)  # probe + reset
    assert hass.states.get(CLIMATE).state == "unavailable"

    mock_uss.read.return_value = [make_status_frame(target_temp=22)]
    await _tick(hass, freezer)
    climate = hass.states.get(CLIMATE)
    assert climate.state == "cool"
    assert climate.attributes["temperature"] == 22.0


async def test_coordinator_builds_profile_from_digital_model(hass: HomeAssistant, mock_uss) -> None:
    """A config entry with a stored cloud digital model self-builds the profile from it."""
    import json as _json

    from custom_components.haismart.const import CONF_DIGITAL_MODEL
    from custom_components.haismart.coordinator import _build_profile, _load_digital_model

    # a model whose fan enum differs from the hardcoded AAC1UKZ01 profile — proves it's used
    model = {"attributes": [
        {"name": "windSpeed", "valueRange": {"type": "LIST", "dataList": [
            {"data": "1", "desc": "高"}, {"data": "5", "desc": "自动"}]}},
        {"name": "operationMode", "valueRange": {"type": "LIST", "dataList": [
            {"data": "1", "desc": "制冷"}]}},
    ]}
    entry = _entry(**{CONF_DIGITAL_MODEL: _json.dumps(model)})
    prof = _build_profile(entry, "AAC1UKZ01", _load_digital_model(entry))
    assert prof.fan_values == {"1": "high", "5": "auto"}  # from the model, not the 4-val default
    assert prof.mode_values == {"1": "cool"}


async def test_coordinator_falls_back_to_hardcoded_profile(hass: HomeAssistant, mock_uss) -> None:
    from haismart_hrdp import profile_for

    from custom_components.haismart.coordinator import _build_profile, _load_digital_model

    entry = _entry()  # no digital model stored
    prof = _build_profile(entry, "AAC1UKZ01", _load_digital_model(entry))
    assert prof.fan_values == profile_for("AAC1UKZ01").fan_values


# --- per-model write lockdown (validate_write wired into the send path) ---------------------------
# A deliberately restrictive model: temperature capped at 24, only cool + auto-fan. The capture
# allowlist would still accept e.g. mode=dry or temp=30, but the device model must veto them.
_LOCKED_MODEL = {"attributes": [
    {"name": "targetTemperature", "writable": True, "valueRange": {
        "type": "STEP", "dataStep": {"minValue": "16", "maxValue": "24", "step": "1"}}},
    {"name": "operationMode", "writable": True, "valueRange": {
        "type": "LIST", "dataList": [{"data": "1", "desc": "cool"}]}},
    {"name": "windSpeed", "writable": True, "valueRange": {
        "type": "LIST", "dataList": [{"data": "5", "desc": "auto"}]}},
]}


async def _setup_with_model(hass: HomeAssistant, model: dict):
    import json as _json

    from custom_components.haismart.const import CONF_DIGITAL_MODEL

    entry = _entry(**{CONF_DIGITAL_MODEL: _json.dumps(model)})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_model_rejects_out_of_range_temperature(hass: HomeAssistant, mock_uss) -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    entry = await _setup_with_model(hass, _LOCKED_MODEL)
    with pytest.raises(HomeAssistantError, match="device model"):
        await entry.runtime_data.async_send_control({"targetTemperature": 30 - 16})
    assert mock_uss.send.await_count == 0  # rejected before any write


async def test_model_rejects_unsupported_enum(hass: HomeAssistant, mock_uss) -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    entry = await _setup_with_model(hass, _LOCKED_MODEL)
    # dry (2) passes the capture allowlist, but the model lists only cool -> the model vetoes it
    with pytest.raises(HomeAssistantError, match="device model"):
        await entry.runtime_data.async_send_control({"operationMode": 2})
    assert mock_uss.send.await_count == 0


async def test_model_allows_in_range_control(hass: HomeAssistant, mock_uss) -> None:
    entry = await _setup_with_model(hass, _LOCKED_MODEL)
    await entry.runtime_data.async_send_control({"targetTemperature": 22 - 16})
    assert mock_uss.send.await_count == 1  # 22 is within [16, 24] -> sent


async def test_device_specific_field_skips_model_gate(hass: HomeAssistant, mock_uss) -> None:
    # ecoMode isn't a standard model attribute, so the model gate must not touch it — the capture
    # allowlist stays its sole gate (a valid eco level still sends under the restrictive model).
    entry = await _setup_with_model(hass, _LOCKED_MODEL)
    await entry.runtime_data.async_send_control({"ecoMode": 6})
    assert mock_uss.send.await_count == 1


# The SHAPE the real cloud model actually uses: booleans are LIST
# enums with codes 'false'/'true' (NOT 0/1), and Haier flags several confirmed grSetDAC fields
# (targetTemperature, rapidMode) as writable=False. Regression guard for both fixes.
_REAL_SHAPE_MODEL = {"attributes": [
    {"name": "targetTemperature", "writable": False, "valueRange": {  # read-only in cloud model...
        "type": "STEP", "dataStep": {"minValue": "16", "maxValue": "30", "step": "1"}}},
    {"name": "operationMode", "writable": True, "valueRange": {
        "type": "LIST", "dataList": [{"data": c} for c in ("0", "1", "2", "6")]}},
    {"name": "screenDisplayStatus", "writable": True, "valueRange": {
        "type": "LIST", "dataList": [{"data": "false"}, {"data": "true"}]}},
    {"name": "rapidMode", "writable": False, "valueRange": {  # ...yet observed in real app writes
        "type": "LIST", "dataList": [{"data": "false"}, {"data": "true"}]}},
]}


async def test_boolean_switch_maps_to_false_true_code(hass: HomeAssistant, mock_uss) -> None:
    # The exact bug: screenDisplayStatus=1 must validate against the model's ['false','true'] codes
    # (was rejected as "screenDisplayStatus='1' not in allowed ['false', 'true']").
    entry = await _setup_with_model(hass, _REAL_SHAPE_MODEL)
    await entry.runtime_data.async_send_control({"screenDisplayStatus": 1})
    assert mock_uss.send.await_count == 1


async def test_model_writable_false_field_still_sends(hass: HomeAssistant, mock_uss) -> None:
    # targetTemperature and rapidMode are writable=False in the cloud model but confirmed —
    # the send path authorizes writability via the capture allowlist, gating only the valueRange.
    entry = await _setup_with_model(hass, _REAL_SHAPE_MODEL)
    await entry.runtime_data.async_send_control({"targetTemperature": 25 - 16})
    await entry.runtime_data.async_send_control({"rapidMode": 1})
    assert mock_uss.send.await_count == 2


async def test_model_valuerange_still_enforced_when_writable_bypassed(
    hass: HomeAssistant, mock_uss
) -> None:
    # Bypassing the writable flag must NOT weaken valueRange gating: a temp above the STEP max is
    # still vetoed even though the field is writable=False.
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    entry = await _setup_with_model(hass, _REAL_SHAPE_MODEL)
    with pytest.raises(HomeAssistantError, match="device model"):
        await entry.runtime_data.async_send_control({"targetTemperature": 40 - 16})
    assert mock_uss.send.await_count == 0


async def test_localkey_backup_sensor_disabled_by_default(
    hass: HomeAssistant, mock_uss
) -> None:
    """The localKey backup sensor exists but is diagnostic + disabled (it's a secret)."""
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import entity_registry as er

    await _setup(hass)
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id("sensor", DOMAIN, "A1B2C3D4E5F6_local_key")
    assert eid is not None
    ent = reg.entities[eid]
    assert ent.disabled_by is not None                       # opt-in
    assert ent.entity_category == EntityCategory.DIAGNOSTIC
    assert hass.states.get(eid) is None                      # no state while disabled


async def test_localkey_backup_sensor_exposes_key_when_enabled(
    hass: HomeAssistant, mock_uss
) -> None:
    """Enabled, it exposes the key + the manual-onboarding fields for backup."""
    from homeassistant.helpers import entity_registry as er

    entry = await _setup(hass)
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id("sensor", DOMAIN, "A1B2C3D4E5F6_local_key")
    reg.async_update_entity(eid, disabled_by=None)           # user enables it
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    st = hass.states.get(eid)
    assert st is not None
    assert st.state == "00112233445566778899aabbccddeeff"    # the localKey
    assert st.attributes["device_id"] == "A1B2C3D4E5F6"
    assert st.attributes[CONF_HOST] == "192.168.1.50"
    assert st.attributes[CONF_LOCALKEY_VERSION] == 4


async def test_diagnostics_redacts_cloud_credentials(hass: HomeAssistant, mock_uss) -> None:
    """An entry WITH cloud credentials must not leak them.

    The pre-existing redaction test used a fixture with no tokens at all, so it could not observe
    that `refresh_token`, `access_token` and `cloud_client_id` were being published in full - in the
    very artefact users are told to attach to GitHub issues. `refresh_token` is durable and
    reusable, so leaking it hands over the whole Haier account.
    """
    import json as _json

    from custom_components.haismart.const import (
        CONF_ACCESS_TOKEN,
        CONF_CLOUD_CLIENT_ID,
        CONF_REFRESH_TOKEN,
    )
    from custom_components.haismart.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    secrets = {
        CONF_REFRESH_TOKEN: "refresh-token-do-not-leak",
        CONF_ACCESS_TOKEN: "access-token-do-not-leak",
        CONF_CLOUD_CLIENT_ID: "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
    }
    entry = _entry(**secrets)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)
    dumped = _json.dumps(diag)
    for key, value in secrets.items():
        assert diag["entry"][key] == "**REDACTED**", f"{key} was not redacted"
        assert value not in dumped, f"{value!r} leaked elsewhere in the diagnostics"
