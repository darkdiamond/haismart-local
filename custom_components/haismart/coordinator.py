"""Polling coordinator built on the uSS read cycle.

Each refresh is one short-lived uSS session (hello handshake -> the AC pushes its status ->
close), exactly the flow verified live on the real ACs. Polling is the RIGHT fit, not a fallback:
the AC has a fixed ~17s session cap anchored to the handshake (not an idle timer — a keepalive
does not extend it), so it is built for short request/response sessions, not held-open ones. And a
write self-confirms — the AC returns its updated state on the op's own connection (the protocol
§2.1). So polling only exists to catch out-of-band changes (physical remote / the app).

A stale localKey is SILENT at the transport level — the handshake still succeeds and only the
biz-data MD5 check fails, so a read cycle just yields no decodable status. To tell rotation
apart from a transient miss, after consecutive empty cycles we probe the AC's current localKey
version (key-free) and compare it with the version recorded at config time; a mismatch raises
ConfigEntryAuthFailed so HA starts a reauth flow for the new key.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from functools import partial
from typing import Any

from haismart_extractor import (
    GatewayCreds,
    GatewayError,
    HaierCloud,
    get_localkey_via_gateway,
)
from haismart_extractor.cloud import SEA_APP_CREDENTIALS, CloudError
from haismart_hrdp import (
    STATUS_LAYOUTS,
    AttributeProfile,
    async_read_status,
    async_send_op,
    grsetdac_baseline_from_status,
    grsetdac_op_frame,
    parse_full_status,
    probe_localkey_version,
    profile_for,
    profile_from_device_config,
    read_grsetdac_field,
    set_grsetdac_field,
    validate_write,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cloud_transport import async_cloud_transport
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLOUD_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_DIGITAL_MODEL,
    CONF_GATEWAY_USERNAME,
    CONF_HOST,
    CONF_LOCAL_KEY,
    CONF_LOCALKEY_VERSION,
    CONF_PRODUCT_CODE,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_ZONE_INFO,
    DEFAULT_PRODUCT_CODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_STALE_LOCALKEY,
    ISSUE_UNKNOWN_LAYOUT,
    READ_TIMEOUT,
    WRITE_TIMEOUT,
)

# Socket timeout for the cloud MQTT-gateway localKey fetch (TLS connect + one round-trip).
GATEWAY_TIMEOUT = 8.0

_LOGGER = logging.getLogger(__name__)

type HaismartConfigEntry = ConfigEntry["HaismartCoordinator"]

# Empty read cycles tolerated before probing the AC's localKey version for rotation.
_MISSES_BEFORE_PROBE = 2

# Caps on the undecodable-frame debug log (the known reports are 125/127 bytes, so this keeps whole
# frames while bounding the damage if some other device pushes something large).
_LOG_FRAME_BYTES = 192
_LOG_FRAME_MAX = 3


# A control op carries raw EPP values; the digital model describes each attribute in STD values.
# For fields that map 1:1 to a model attribute (same STD name), this converts the EPP value so
# ``validate_write`` can gate it against the model's ``valueRange``:
#   - temperature is absolute degC (EPP + 16), matched against the STEP range;
#   - enums are the STD code directly (grSetDAC uses the model's numeric codes: operationMode
#     ``0/1/2/6``, windSpeed ``1/2/3/5``), matched by string against the LIST codes;
#   - booleans map the grSetDAC bit 0/1 to the model's LIST codes ``'false'``/``'true'`` — the model
#     describes these as string enums, NOT 0/1, so a raw-int passthrough was rejected as e.g.
#     ``screenDisplayStatus='1' not in ['false','true']``.
# Device-specific fields (the vertical-swing toggle 0x0c and this unit's repurposed 3-bit
# ``ecoMode``) have no standard model attribute, so they are absent here and stay gated by the
# encoder allowlist in ``set_grsetdac_field`` alone.
def _bool_code(epp: int) -> str:
    return "true" if epp else "false"


_MODEL_VALUE_FROM_EPP: dict[str, Callable[[int], object]] = {
    "targetTemperature": lambda epp: epp + 16,
    "operationMode": lambda epp: epp,
    "windSpeed": lambda epp: epp,
    "onOffStatus": _bool_code,
    "healthMode": _bool_code,
    "rapidMode": _bool_code,
    "muteStatus": _bool_code,
    "silentSleepStatus": _bool_code,
    "screenDisplayStatus": _bool_code,
    # raw EPP value == the STD code the model lists (0 / 7), so the valueRange gate applies
    # directly. windDirectionVertical is deliberately absent: its EPP nibble (0x0c) is NOT its
    # STD code (8), so it cannot be validated against the model's valueRange.
    "windDirectionHorizontal": lambda epp: epp,
}


def _load_digital_model(entry: HaismartConfigEntry) -> dict[str, Any] | None:
    """The cloud-fetched digital model (device constraints) as a dict, or None if absent/bad."""
    raw = entry.data.get(CONF_DIGITAL_MODEL)
    if not raw:
        return None
    try:
        model = json.loads(raw)
    except (ValueError, TypeError):
        _LOGGER.warning("stored digital model is not valid JSON; model write-validation disabled")
        return None
    return model if isinstance(model, dict) and model.get("attributes") else None


def _build_profile(
    entry: HaismartConfigEntry, product_code: str, model: dict[str, Any] | None
) -> AttributeProfile:
    """Prefer the cloud-fetched digital model (correct for ANY model); otherwise fall back to the
    hardcoded per-model profile keyed by product_code."""
    if model is not None:
        try:
            return profile_from_device_config(model)
        except (ValueError, KeyError, TypeError):
            _LOGGER.warning("stored digital model is unusable; using the hardcoded profile")
    return profile_for(product_code)


class HaismartCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one AC over uSS and exposes the parsed full-status report."""

    config_entry: HaismartConfigEntry

    def __init__(self, hass: HomeAssistant, entry: HaismartConfigEntry) -> None:
        self.host: str = entry.data[CONF_HOST]
        self.device_id: str = entry.data[CONF_DEVICE_ID]
        self._local_key: str = entry.data[CONF_LOCAL_KEY]
        self.product_code: str = entry.data.get(CONF_PRODUCT_CODE) or DEFAULT_PRODUCT_CODE
        self.digital_model: dict[str, Any] | None = _load_digital_model(entry)
        self.profile: AttributeProfile = _build_profile(
            entry, self.product_code, self.digital_model
        )
        self.localkey_version: int | None = entry.data.get(CONF_LOCALKEY_VERSION)
        self.last_raw_status: bytes | None = None
        self._misses = 0
        # length of a status report we could only partially decode, or None. Drives the repair.
        self.unknown_layout: int | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.device_id}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            blobs = await async_read_status(
                self.host, self.device_id, self._local_key, timeout=READ_TIMEOUT
            )
        except (TimeoutError, OSError, RuntimeError) as err:
            raise UpdateFailed(f"uSS read from {self.host} failed: {err}") from err

        for blob in blobs:
            if state := parse_full_status(blob, self.profile, self.digital_model):
                self._misses = 0
                self.last_raw_status = blob
                if state.get("partial"):
                    # Decoded, but only the layout-independent fields: this model's report
                    # length has no confirmed layout. Keeping the blob matters -- it is exactly
                    # what a maintainer needs, and diagnostics used to report `null` for this case.
                    self._note_unknown_layout(blob)
                else:
                    self._clear_unknown_layout()
                return state

        # Connected fine but nothing decoded — either the AC pushed no full report this
        # cycle (transient) or every biz payload failed the MD5 check (stale localKey).
        self._log_undecodable(blobs)
        self._misses += 1
        # capture BEFORE the probe below resets it, or the message always reports 0
        misses = self._misses
        if self._misses >= _MISSES_BEFORE_PROBE:
            # probe once at the threshold; if the key still matches it's a transient miss, so
            # reset the counter rather than re-probe (an extra handshake) on every later cycle.
            await self._check_localkey_rotation()
            self._misses = 0
        raise UpdateFailed(
            f"no decodable status from {self.host} ({misses} consecutive misses)"
        )

    def _note_unknown_layout(self, blob: bytes) -> None:
        """Record an unrecognised report length: log once, raise a repair, remember the blob."""
        if self.unknown_layout == len(blob):
            return      # already reported; do not repeat every poll
        self.unknown_layout = len(blob)
        _LOGGER.warning(
            "Unrecognised Haier status report from %s: %d bytes (known: %s). Power, setpoint, "
            "mode, fan and vertical swing were decoded; indoor/outdoor temperature and the "
            "secondary toggles are unavailable. product_code=%s. Please report this model so the "
            "layout can be added - see docs/new-model.md.",
            self.host, len(blob), ", ".join(str(n) for n in sorted(STATUS_LAYOUTS)),
            self.product_code,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_UNKNOWN_LAYOUT}_{self.device_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_UNKNOWN_LAYOUT,
            translation_placeholders={
                "name": self.config_entry.title,
                "length": str(len(blob)),
                "product_code": self.product_code or "unknown",
            },
            learn_more_url=(
                "https://github.com/darkdiamond/haismart-local/blob/main/docs/new-model.md"
            ),
        )

    def _clear_unknown_layout(self) -> None:
        if self.unknown_layout is None:
            return
        self.unknown_layout = None
        ir.async_delete_issue(self.hass, DOMAIN, f"{ISSUE_UNKNOWN_LAYOUT}_{self.device_id}")

    def _log_undecodable(self, blobs: list[bytes]) -> None:
        """Debug-log what the AC actually sent when no status decoded — the report discriminator.

        ``async_read_status`` returns only payloads that decrypted (a failed biz MD5 check is
        dropped silently), so the two cases look identical from the outside but mean opposite
        things:

        * **no payloads** — the AC pushed nothing this cycle, OR every payload failed the MD5 check,
          i.e. the localKey is wrong/stale (the consecutive-miss probe below checks for rotation);
        * **payloads present** — the localKey is GOOD and nothing the AC sent was a full-status
          report at all: a frame without the ``2715`` signature, or one too short for even the
          layout-independent fields.

        Note an unrecognised report *length* does NOT reach here: ``parse_full_status`` decodes
        those partially and :meth:`_note_unknown_layout` raises a repair, so a new model is already
        diagnosed by name. What lands here is whatever else the AC is pushing, hence logging the
        frames in full — they are the only way to identify it.

        Report bytes carry device state only, no key material (the same bytes diagnostics exports).
        """
        if not blobs:
            _LOGGER.debug(
                "%s: handshake OK but nothing decrypted this cycle (stored localKey v%s) — either "
                "the AC pushed no status, or every payload failed the biz MD5 check "
                "(wrong/stale key)",
                self.device_id,
                self.localkey_version,
            )
            return
        _LOGGER.debug(
            "%s: localKey is good (%d payload(s) decrypted) but no full-status report decoded — "
            "unrecognised frame (no 2715 signature, or shorter than the attribute vector). "
            "Frames: %s",
            self.device_id,
            len(blobs),
            "; ".join(
                f"len={len(b)} {b[:_LOG_FRAME_BYTES].hex()}"
                f"{'…' if len(b) > _LOG_FRAME_BYTES else ''}"
                for b in blobs[:_LOG_FRAME_MAX]
            ),
        )

    async def async_send_control(self, changes: dict[str, int]) -> None:
        """Apply ``{field_name: raw_epp_value}`` to the state and send it as one grSetDAC op.

        grSetDAC is a group-set: it must be seeded from the AC's TRUE current state so every
        attribute except the changed one(s) is preserved, then the requested fields flipped. We
        seed from the status the AC pushes on the op's OWN connection right after the handshake
        (``build_frame``), so the baseline is live — no separate read connection (keeps control
        snappy, halves the AC load) and no staleness: seeding from a cached ``last_raw_status``
        that lags an IR change or a prior command could re-send an old power/mode bit and silently
        turn the unit back off. If the AC pushes nothing that session we fall back to the cached
        status. The library gates each field + value (confirmed only). WRITES to the AC.
        """
        # Per-model lockdown: gate every change against the device's digital model (valueRange)
        # BEFORE it is sent, on top of the library's confirmed allowlist — so the pulled
        # product constraints reject an out-of-range temperature or an unsupported enum. Only
        # fields mapping 1:1 to a model attribute are checked; device-specific ones (swing/eco) stay
        # gated by the encoder allowlist alone.
        self._validate_against_model(changes)

        if self.unknown_layout is not None:
            # Reads degrade gracefully on an unrecognised report; writes must not. The size of the
            # control-word block is exactly what could not be determined, so a group-set built from
            # it could send a read-only sensor byte back to the AC as a setting.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="layout_unknown",
                translation_placeholders={
                    "name": self.config_entry.title,
                    "length": str(self.unknown_layout),
                },
            )

        def _build(baseline: bytes | None) -> bytes:
            base = baseline if baseline is not None else self.last_raw_status
            if base is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_status",
                    translation_placeholders={"name": self.config_entry.title},
                )
            if baseline is not None:  # refresh the cache from the fresh in-session baseline
                self._misses = 0
                self.last_raw_status = baseline
            words = grsetdac_baseline_from_status(base)
            for name, value in changes.items():
                words = set_grsetdac_field(words, name, value)
            return grsetdac_op_frame(words)

        try:
            # One short session: the CAE counter starts at 1 and the biz sequence base is
            # auto-derived from HELLO_DONE_RESP (a wrong sn drops the connection). build_frame
            # seeds from the AC's in-session status push.
            reply = await async_send_op(
                self.host, self.device_id, self._local_key,
                build_frame=_build, counter=1, timeout=WRITE_TIMEOUT,
            )
        except HomeAssistantError:
            raise
        except (ValueError, KeyError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="control_rejected",
                translation_placeholders={"name": self.config_entry.title, "error": str(err)},
            ) from err
        except (OSError, RuntimeError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="control_failed",
                translation_placeholders={"name": self.config_entry.title, "error": str(err)},
            ) from err
        # The AC echoes its UPDATED state on the op's own connection (the protocol), so confirm
        # from that reply directly — instant, one fewer connection. Fall back to a read cycle only
        # if the reply carried no decodable full-status report.
        if (state := self._state_from_reply(reply)) is not None:
            self.async_set_updated_data(state)
        else:
            await self.async_request_refresh()

    def _state_from_reply(self, reply: list[bytes]) -> dict[str, Any] | None:
        """The newest decodable full-status report in a control op's reply blobs, or None if none
        decoded. The AC echoes updated state on the op connection (the protocol). Also updates
        the seed baseline + miss counter so the next op/poll starts from the confirmed state."""
        for blob in reversed(reply):
            if state := parse_full_status(blob, self.profile, self.digital_model):
                self.last_raw_status = blob
                self._misses = 0
                return state
        return None

    def _validate_against_model(self, changes: dict[str, int]) -> None:
        """Reject a control change the device's digital model forbids (out-of-range temperature, an
        enum the unit doesn't support, a non-writable attribute). No-op when no digital model is
        stored (e.g. the manual onboarding path) or for device-specific fields the model doesn't
        describe — those stay gated by the library's encoder allowlist. Raises HomeAssistantError.
        """
        model = self.digital_model
        if model is None:
            return
        described = {a.get("name") for a in model.get("attributes", [])}
        for name, epp in changes.items():
            to_model = _MODEL_VALUE_FROM_EPP.get(name)
            # skip device-specific fields, and fields the model doesn't describe (can't constrain
            # what it doesn't list — the encoder allowlist still gates those). Enforce the rest.
            if to_model is None or name not in described:
                continue
            # Gate the valueRange only. The model's own ``writable`` flag misclassifies several
            # confirmed grSetDAC fields (targetTemperature, rapidMode, muteStatus,
            # silentSleepStatus) as read-only; writability here is authorized by the capture
            # allowlist in ``set_grsetdac_field``, which is proven against real hardware.
            ok, reason = validate_write(model, name, to_model(epp), require_writable=False)
            if not ok:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="control_rejected",
                    translation_placeholders={
                        "name": self.config_entry.title,
                        "error": reason,
                    },
                )

    @property
    def local_key(self) -> str:
        """The AC's current localKey (kept fresh across gateway auto-refresh). For the opt-in backup
        sensor — it's a secret, so that entity is diagnostic + disabled by default."""
        return self._local_key

    def current_field(self, name: str) -> int | None:
        """The live raw EPP value of a grSetDAC field (for the toggle/select entities), or None."""
        if self.last_raw_status is None:
            return None
        try:
            return read_grsetdac_field(self.last_raw_status, name)
        except (ValueError, KeyError):
            return None

    async def _check_localkey_rotation(self) -> None:
        """Probe the AC's current localKey version (key-free). On rotation, try to auto-refresh the
        localKey from the Haier cloud MQTT gateway; only fall back to a manual reauth flow if the
        gateway refresh isn't configured or fails."""
        try:
            current = await self.hass.async_add_executor_job(
                partial(
                    probe_localkey_version, self.host, self.device_id, timeout=READ_TIMEOUT
                )
            )
        except (OSError, RuntimeError) as err:
            raise UpdateFailed(f"localKey version probe failed: {err}") from err
        if self.localkey_version is None or current == self.localkey_version:
            return
        old = self.localkey_version
        if await self._async_gateway_refresh():
            _LOGGER.info(
                "localKey auto-refreshed via the cloud gateway (v%s -> v%s) for %s",
                old, self.localkey_version, self.device_id,
            )
            self.clear_stale_localkey_issue()  # healed itself — no manual step needed
            return
        # No cloud creds to self-heal: a person must reauth by hand. Surface an actionable repair
        # advising them to add account credentials so future rotations auto-refresh.
        self._raise_stale_localkey_issue(old, current)
        raise ConfigEntryAuthFailed(
            f"localKey rotated on the AC (v{old} -> v{current}) and no cloud auto-refresh "
            "succeeded; a fresh key is needed"
        )

    def _raise_stale_localkey_issue(self, old: int | None, current: int) -> None:
        """Create the actionable repair for a manual re-key (no cloud auto-refresh configured)."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_STALE_LOCALKEY}_{self.device_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_STALE_LOCALKEY,
            translation_placeholders={
                "name": self.config_entry.title,
                "old": str(old),
                "new": str(current),
            },
        )

    def clear_stale_localkey_issue(self) -> None:
        """Delete the manual-re-key repair once rotation self-heals via the cloud gateway."""
        ir.async_delete_issue(
            self.hass, DOMAIN, f"{ISSUE_STALE_LOCALKEY}_{self.device_id}"
        )

    async def _async_gateway_refresh(self) -> bool:
        """Fetch the current localKey from the cloud MQTT gateway and update it in place.

        Returns ``True`` on success (key + version updated on ``self`` and persisted to the config
        entry, so the next read cycle uses it); ``False`` if the gateway credentials aren't
        configured or any step fails — the caller then falls back to a manual reauth flow. Every
        CONNECT credential is now DERIVED (nothing stored): the clientId from the stored uSDK
        CLIENTID, the accessToken minted from the reusable refreshToken, and the MQTT
        username/password by ``haismart_extractor.gateway.derive_gateway_auth`` — so the only
        per-entry input needed is the uSDK CLIENTID + a token. (``CONF_GATEWAY_USERNAME`` /
        ``CONF_GATEWAY_PASSWORD`` are honored if present, for pinning, but no longer required.)
        """
        data = self.config_entry.data
        usdk_client_id = data.get(CONF_CLOUD_CLIENT_ID)
        if not usdk_client_id:
            return False  # gateway auto-refresh not configured for this entry
        # Optional pin: if an explicit username was stored, reuse its body so the pair is
        # reproducible; otherwise a fresh valid pair is generated per refresh.
        pinned_username = data.get(CONF_GATEWAY_USERNAME)
        username_body = (
            pinned_username[2:]
            if pinned_username and pinned_username.startswith("01") and len(pinned_username) == 10
            else None
        )

        access_token = data.get(CONF_ACCESS_TOKEN)
        refresh_token = data.get(CONF_REFRESH_TOKEN)
        if refresh_token:
            # mint a fresh accessToken from the durable refreshToken (accessTokens expire ~daily)
            try:
                cloud = HaierCloud(
                    replace(SEA_APP_CREDENTIALS, client_id=usdk_client_id),
                    access_token or "",
                    zone_info=data.get(CONF_ZONE_INFO, "0"),
                    # HA's shared httpx client: building one here would block the loop (CA bundle)
                    transport=async_cloud_transport(self.hass),
                )
                access_token = (await cloud.refresh_token(refresh_token)).access_token
            except (CloudError, OSError, RuntimeError) as err:
                _LOGGER.warning("token refresh failed (%s); trying the stored access token", err)
        if not access_token:
            return False

        creds = GatewayCreds.derive(
            usdk_client_id=usdk_client_id,
            access_token=access_token,
            username_body=username_body,
        )
        try:
            local_key = await self.hass.async_add_executor_job(
                partial(get_localkey_via_gateway, creds, self.device_id, timeout=GATEWAY_TIMEOUT)
            )
        except (GatewayError, OSError, RuntimeError) as err:
            _LOGGER.warning("gateway localKey refresh failed for %s: %s", self.device_id, err)
            return False

        self._local_key = local_key.key
        self.localkey_version = local_key.version
        updates: dict[str, Any] = {
            CONF_LOCAL_KEY: local_key.key,
            CONF_LOCALKEY_VERSION: local_key.version,
        }
        if access_token and access_token != data.get(CONF_ACCESS_TOKEN):
            updates[CONF_ACCESS_TOKEN] = access_token
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**data, **updates}
        )
        return True
