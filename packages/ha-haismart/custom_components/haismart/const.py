"""Constants for the Haismart local integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "haismart"

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
]

CONF_HOST = "host"
CONF_DEVICE_ID = "device_id"
CONF_LOCAL_KEY = "local_key"
CONF_NAME = "name"
# Cloud credential provisioned by the email/password Login onboarding path (the durable, reusable
# refreshToken + the account access token + per-install clientId + region). The coordinator uses
# these to auto-refresh a rotated localKey from the cloud gateway; the localKey itself stays local.
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN = "access_token"
CONF_CLOUD_CLIENT_ID = "cloud_client_id"  # per-install uSDK CLIENTID (32-hex), the token's terminal
CONF_ZONE_INFO = "zone_info"
# Optional MQTT-gateway CONNECT credentials. No longer required: the coordinator derives every
# gateway credential (`_async_gateway_refresh` + `haismart_extractor.gateway.derive_gateway_auth`) —
# clientId from CONF_CLOUD_CLIENT_ID, token from CONF_REFRESH_TOKEN, and the username/password from
# the derivation formula. These stay only to optionally PIN a username body; leave them blank.
CONF_GATEWAY_USERNAME = "gateway_username"
CONF_GATEWAY_PASSWORD = "gateway_password"
# The device's digital model (JSON string), fetched from the cloud during discovery. When present
# the coordinator self-builds the AttributeProfile from it (correct for ANY model); else it falls
# back to the hardcoded profile_for(product_code).
CONF_DIGITAL_MODEL = "digital_model"
# Cloud product_code/pid (e.g. AAC1UKZ01) — selects the AttributeProfile for status decode.
CONF_PRODUCT_CODE = "product_code"
# Human-readable identity from the cloud device list's `extendedInfo` (prodNo/model/brand). Shown on
# the HA device page instead of the raw product code.
CONF_MODEL_NAME = "model_name"
CONF_BRAND = "brand"
# The AC's localKey version at config time (HELLO_RESP payload). The key rotates server-side;
# a version mismatch on a later probe means the cached key is stale -> reauth.
CONF_LOCALKEY_VERSION = "localkey_version"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30  # seconds between read cycles (each is handshake+collect+close)
MIN_SCAN_INTERVAL = 10
DEFAULT_PRODUCT_CODE = "AAC1UKZ01"
READ_TIMEOUT = 4.0  # per-connection socket timeout used by the uSS read cycle
WRITE_TIMEOUT = 5.0  # per-connection socket timeout for a control (grSetDAC) op session

MANUFACTURER = "Haier"

# Haier's `deviceType` encodes the appliance class in its FIRST BYTE, as hex (from Haier's own uSDK
# device-type enum; e.g. 0201201d -> 0x02 = split AC, 21001001 -> 0x21 = air purifier). Used to warn
# when a picked device is not an air conditioner at all.
AC_DEVICE_CLASSES: dict[str, str] = {
    "02": "split AC",
    "03": "cabinet AC",
    "0d": "commercial AC",
    "39": "window AC",
}

# Repairs: raised when the localKey rotated but the entry has no cloud credentials to self-heal, so
# the user must reauth by hand. Advises adding account creds so rotation auto-refreshes in future.
ISSUE_STALE_LOCALKEY = "stale_localkey_manual_reauth"

# mDNS service the AC's wifi module announces (instance name = deviceId, e.g. A1B2C3D4E5F6).
ZEROCONF_TYPE = "_cae._udp.local."
