# haismart-extractor

Cloud client for the Haismart (Haier SE-Asia) platform. Used by the Home Assistant integration to sign
in and to fetch each AC's per-device `localKey`.

## Modules

| Module | What it is |
|---|---|
| `src/haismart_extractor/cloud.py` | Account **email/password login** + token refresh, device list, digital model. Device-center request signing: `SHA-256(path + strippedBody + appId + appKey + timestamp)`. |
| `src/haismart_extractor/gateway.py` | Per-device **`localKey`** fetch over the cloud MQTT gateway (`gw-sgp.haieriot.net:58702`). All CONNECT credentials are derived (`derive_client_id`, `derive_gateway_auth`). |

The CONNECT `username`/`password` are derived, not stored (`derive_gateway_auth`):
`username = "01" + 8 digits`, `password = hex(AES-128-CBC(MD5(body), iv=0, BE16(9)+"haier_sdk" padded))`.
The gateway recomputes the password from the username, so a freshly generated pair connects.

## Example

```python
import asyncio
from haismart_extractor import HaierCloud, SEA_APP_CREDENTIALS, GatewayCreds, get_localkey_via_gateway

async def main():
    cloud = HaierCloud(SEA_APP_CREDENTIALS)
    login = await cloud.login("you@example.com", "password", zone="66")
    creds = GatewayCreds.derive(usdk_client_id=login.client_id, access_token=login.access_token)
    key = get_localkey_via_gateway("ACB722AABBCC", creds)
    print(key)

asyncio.run(main())
```

## Tests

```bash
python -m pytest
```
