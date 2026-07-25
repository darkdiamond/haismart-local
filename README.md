# haismart-local

> ### Fork notes
>
> This is a fork of [enapt/haismart-local](https://github.com/enapt/haismart-local) with two
> additions, both verified against real hardware:
>
> - **125-byte status report support** (`deviceType 0201201d`). Upstream gated on a 127-byte
>   report, so these units failed every poll with `no decodable status`, which looks
>   misleadingly like a stale `localKey`. Report layouts are now a table keyed by length.
> - **Horizontal swing** (`windDirectionHorizontal`, grSetDAC word 4 bits 0-2). Previously only
>   vertical swing was mapped, so left-right was invisible and uncontrollable. Swing is now the
>   conventional single control with **off / vertical / horizontal / both**, and both axes are
>   written in one group-set so a change can't land half-applied.
>
> Upstream has pull requests disabled, so these changes live here rather than being contributed
> back.

Fully-local **Home Assistant** control for Haier air conditioners that pair with the **Haismart**
(Haier U+ / uHome, SE-Asia) app. After a one-time sign-in, reads and control run entirely on your LAN —
no cloud at runtime.

## Features

Per AC:
- **Climate** — target temperature, HVAC mode (cool / dry / fan-only / auto), fan speed, swing, on/off.
- **Switches** — strong, quiet, health, sleep, display light.
- **Eco** select.
- **Indoor / outdoor temperature** sensors.
- **DHCP discovery** (the device id is the unit's Wi-Fi MAC, OUI `AC:B7:22`).
- **Automatic key refresh** — the per-device key rotates server-side; the integration re-fetches it and
  keeps working without a manual re-entry.

## How it works

Each AC exposes a local control protocol over TCP `:56800`. You sign in to your Haismart account once so
the integration can fetch each AC's per-device `localKey`; from then on it discovers the unit on the LAN and
drives it directly — status reads and control commands never leave your network. Control is a group-set
write seeded from the AC's own live status, so a change preserves every other setting.

## Requirements

- **Home Assistant** (a reasonably recent release), installed via HACS or a manual `custom_components` copy.
- A Haier AC that pairs with the **Haismart** (Haier U+ / uHome, SE-Asia) app — its Wi-Fi module speaks the
  local uSS protocol on TCP `:56800` (module MAC OUI `AC:B7:22`).
- Home Assistant and the AC on the **same LAN**.
- Verified on **HSU-24VRRA03TF** (typeId `AAC1UKZ01`). Other Haismart-paired Haier ACs are likely supported —
  the integration builds its entities from the unit's cloud **digital model** — but are untested.

## Onboarding

Install via **HACS** (add this repo as a custom repository → category *Integration*) or copy the root
`custom_components/haismart/` into `config/custom_components/` — it's self-contained, no pip step. Then
add the integration in **Settings → Devices & Services → Add Integration → "Haismart (Haier local)"** and
pick one:

- **Login (recommended)** — email/phone + password + region code. Lists your ACs and fetches the key for the
  one you pick automatically; finds its IP by DHCP.
- **Manual** — host + device id + `localKey` directly (fully offline).

> **Google / Facebook accounts** have no password to sign in with. Create a throwaway **email/password**
> Haier account, **share your AC(s) to it** in the Haismart app, then use **Login** with that account
> (sharing grants the same local access as ownership).

Full install + onboarding steps: [`INSTALL.md`](INSTALL.md).

## Cloud independence (optional)

Everything runs locally after setup — reads and control never touch Haier. The *only* runtime cloud
dependency is that Haier's server can **rotate** a unit's `localKey`, which the integration then re-fetches.
If you'd rather your ACs never phone home at all, block Haier's cloud at your DNS/router — the key can then
never rotate and your stored key stays valid indefinitely. A rotation is pushed to the AC over *its own*
cloud connection, so block **the AC's** internet (keep its LAN open — HA still needs `:56800`). Archive each
key first (the disabled **Local key** diagnostic sensor exports it) so you can always re-add a unit via the
offline **Manual** path, no cloud. Step-by-step (domain list, gateway IP, per-device WAN option, how to
confirm the key stopped rotating): [**`INSTALL.md` §4**](INSTALL.md#4-optional-go-fully-cloud-independent-block-haiers-servers).

## Credentials

Onboarding uses the app's own sign-in and the app-level constants that are the same for every user —
the same model other Haier integrations use. The closest precedent is
[`banto6/haier`](https://github.com/banto6/haier): it ships the same **uHome** app constants (same
`MB-SHE…` appId format) and the **identical device-center request signing**
(`SHA-256(path + body + appId + appKey + timestamp)`) — it just targets Haier's mainland app, where this
targets the SE-Asia (Haismart) one. The widely-used [pyHon](https://github.com/Andre0512/pyhOn) /
[`hon`](https://github.com/Andre0512/hon) integrations follow the same overall approach on Haier's
hOn/Candy platform. You sign in with your own Haismart account; nothing is bypassed. This is an
interoperability tool for hardware you own, on your own network.

## Packages

| Package | What it is |
|---|---|
| [`packages/haismart-hrdp`](packages/haismart-hrdp) | Standalone async client for the local `:56800` protocol. No Home Assistant dependency. |
| [`packages/haismart-extractor`](packages/haismart-extractor) | Cloud client: account login, per-device `localKey` fetch/refresh over the MQTT gateway, device list, digital model. |
| [`packages/ha-haismart`](packages/ha-haismart) | The Home Assistant integration (config flow, coordinator, climate/switch/select/sensor entities). |

## Use without Home Assistant

`haismart-hrdp` has **no Home Assistant dependency** — it's a plain async Python library, so the same
local protocol the integration uses can drive an AC from any program (a script, a cron job, a Node-RED
`exec` node, an MQTT bridge, …). With a unit's `host` + `deviceId` + `localKey` (the "Manual" trio) you
read and control it directly:

```python
import asyncio, haismart_hrdp as h

HOST, DEVID, KEY = "192.168.1.50", "ACB722AABBCC", "<32-hex-localKey>"

# read
blob = next(b for b in h.read_status(HOST, DEVID, KEY) if len(b) == 127)
print(h.parse_full_status(blob, h.profile_for("AAC1UKZ01")))

# control: set the target temperature to 24 °C, seeded from the AC's own live status so every
# other setting is preserved (single-session read-modify-write)
def build(status):
    words = h.set_grsetdac_field(h.grsetdac_baseline_from_status(status), "targetTemperature", 24 - 16)
    return h.grsetdac_op_frame(words)
asyncio.run(h.async_send_op(HOST, DEVID, KEY, counter=1, build_frame=build))
```

Don't have the `localKey`? Fetch it once with `haismart-extractor` (account login → MQTT-gateway localKey
fetch — the same path the integration's Login onboarding uses). A runnable end-to-end CLI is in
[`examples/standalone_control.py`](examples/standalone_control.py).

## Development

```bash
python3 -m venv --system-site-packages .venv && . .venv/bin/activate
pip install pytest pytest-asyncio cryptography httpx
scripts/test.sh          # runs all three suites
```

## License

See [`LICENSE`](LICENSE).

## Disclaimer

This is an independent, community project. It is **not affiliated with, authorized, or endorsed by
Haier**. "Haier", "Haismart", and "Haier U+" are trademarks of their respective owners, used here only
descriptively to identify the hardware this software interoperates with. Provided as-is, for use with
hardware you own on your own network.

**Your responsibility:** the Login onboarding path communicates with Haier's account API, and
device-sharing (the throwaway-account option) may be governed by the Haismart app's Terms of Service.
You are responsible for your own compliance with those terms. This software is provided as-is, without
warranty of any kind.
