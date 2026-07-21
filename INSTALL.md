# Installing the Haismart integration on your Home Assistant

Fully-local control of Haier ACs that pair with the **Haismart** (Haier U+/uHome SE‑Asia) app — no cloud at
runtime after setup. This guide gets the integration onto a running Home Assistant for real use/testing.

> **Not on HACS yet.** The integration depends on two helper libraries (`haismart-hrdp`, `haismart-extractor`)
> that aren't published to PyPI, so HA can't auto-install them. `scripts/install-dev.sh` handles that by
> pip-installing them into HA's Python env; then HA sees the `manifest.json` requirements already satisfied.

## Prerequisites

- **Home Assistant** running, **on the same LAN as the ACs** (they answer on TCP `:56800`). Runtime is local;
  the cloud is touched only at setup and on key rotation.
- **A Haismart account.** Easiest is an **email/phone + password** account. If you sign in with **Google/
  Facebook** (no password), create a throwaway email/password Haier account, **share your AC(s) to it** in
  the app, and log in with that account — sharing grants the same local access as ownership.
- `cryptography` and `httpx` are already shipped with HA — nothing else to install.

## 1. Install

### HACS (recommended)

In **HACS → ⋮ → Custom repositories**, add `https://github.com/enapt/haismart-local` with category
**Integration**. Install **Haismart (Haier local)**, then **restart Home Assistant**. The component is
self-contained (the two helper libraries are vendored into it), so there is **no pip step**.

### Manual copy

Copy the repo's root [`custom_components/haismart/`](custom_components/haismart) folder into your
`<config>/custom_components/` and restart Home Assistant. It's self-contained — nothing to pip-install.
(This is the same vendored drop-in HACS installs.)

### From source (development)

Run from a checkout of this repo. The script pip-installs the two libs **into HA's Python env** and copies the
component into `<config>/custom_components/`.

### Home Assistant Core / venv
```bash
scripts/install-dev.sh --config ~/.homeassistant --python /srv/homeassistant/bin/python
```
Point `--python` at the interpreter of the venv where `homeassistant` is installed (so the libs land where HA
imports from). `--config` is the directory holding `configuration.yaml`.

### Home Assistant Container (Docker)
Run it **inside** the container so the libs go into the container's Python:
```bash
docker cp . homeassistant:/tmp/haismart-local          # or bind-mount the repo
docker exec -it homeassistant bash -lc \
  'cd /tmp/haismart-local && scripts/install-dev.sh --config /config --python python3'
```

### Home Assistant OS / Supervised
You can't easily pip-install into the core container here — this is the one awkward case. For testing,
use a **Core/venv or Container** instance (even a spare one on the same LAN). Vendoring the two pure-Python
libs into the component is the eventual HAOS fix, but that's packaging work, not covered by this script.

### Options
- `--symlink` — symlink the component instead of copying (edits apply on the next HA restart; handy for dev).
- `--copy-only` — skip the pip step (only if the two libs are already importable in HA's env).
- `PYTHON=<exe>` — same as `--python`.

Then **restart Home Assistant.**

## 2. Add the integration

**Settings → Devices & Services → Add Integration → "Haismart (Haier local)"** (it may also appear on its own
via zeroconf, since it listens for `_cae._udp`). Pick one path:

- **Login (recommended) — no key to paste:** email/phone + password + **region code** (the phone country
  code — e.g. `66` Thailand, `65` Singapore). It then **lists your ACs to pick from**, and for the one you
  choose it **fetches the localKey from the cloud automatically** — you never paste a key — and **finds the
  AC's IP via DHCP** (the deviceId is the AC's MAC). You only enter the IP if HA hasn't seen the AC on the
  network yet (then find it in your router's client list).
  **Google/Facebook owners** (no password): create a throwaway email/password Haier account, **share your
  AC(s) to it** in the app, and log in with that account here.
- **Manual:** host + device ID + `localKey` directly (no cloud; fully offline, but a key rotation then needs a
  manual re-enter instead of self-healing).

The flow validates by doing a live read, then creates the entities.

**Multiple ACs (e.g. Upstairs + Downstairs):** each AC is its own HA device, added one at a time. After you
add the first, run **Add Integration → Haismart** again — the picker now shows only the AC(s) you haven't
added yet (and says so once they're all in). Each AC is also **DHCP-discovered** (its MAC starts `AC:B7:22`),
so both will appear as "Discovered" cards you can add directly.

## 3. Verify

You should get, per AC: a **climate** card (temperature, mode, fan, swing, on/off), five **switches**
(strong / quiet / health / sleep / display light), an **eco** select, and indoor/outdoor **temperature**
sensors. Change the setpoint — the AC applies it and the card confirms from the AC's own reply immediately.

## 4. Optional: go fully cloud-independent (block Haier's servers)

**Everything already runs locally after setup** — reads and control never touch Haier. The *only* runtime
cloud dependency is that Haier's server can **rotate the per-device `localKey`** (which the integration then
re-fetches). If you'd rather the units never phone home at all, you can block Haier's cloud — then the key
can't rotate and your stored key stays valid indefinitely. This is **optional** and for advanced users.

**The key fact:** a key rotation is *pushed to the AC over the AC's own cloud connection*. So you must block
**the AC's** internet access, not just Home Assistant's. Keep the AC's **LAN** open (HA still needs `:56800`).

> **Before blocking:** note each AC's current key/version — from HA go **Settings → Devices & Services →
> Haismart → the device → Diagnostics** (redacted), or run `probe_localkey_version(ip, deviceId)`. Keep a
> copy as your escape hatch; you can always re-add via the **manual** path (host + deviceId + key), no cloud.

### Option A — DNS / domain block (easiest)
On your DNS filter (Pi-hole, AdGuard Home, or the router) block these — they cover every Haier endpoint the
app and the gateway use, including the localKey MQTT gateway `gw-sgp.haieriot.net:58702`:

```
*.haieriot.net
*.haier.net
*.haigeek.com
```

Belt-and-suspenders, also block the gateway **IP** at the firewall — some units hardcode it and skip DNS:

```
43.156.75.60
```

### Option B — per-device WAN block (bulletproof)
On the router, deny **internet (WAN)** for each AC by its **MAC** (`AC:B7:22:xx:xx:xx`) or reserved IP, while
allowing **LAN**. This catches hardcoded IPs and any baked-in DNS automatically — zero doubt — at the cost of
a per-device rule. This is the reliable choice if you want a guarantee.

### Verify it holds
1. Confirm local read/control still works right after blocking (some IoT gear sulks without cloud — these
   shouldn't, since the local session is a separate pipe, but check): change the setpoint in HA.
2. Confirm the key stops rotating — watch `probe_localkey_version` for each AC over a few days; if the version
   stays put, no rotations are getting through and you're fully independent. If it still moves, the AC is
   leaking to the cloud (hardcoded IP / its own DNS) → use Option B.

> **Caveat:** DNS blocking only works if the AC resolves through DNS you control, and the AC's own
> outbound host isn't guaranteed to fall under the three domains above (it's almost certainly a
> `*.haieriot.net` broker). Option B removes both doubts.

## 5. Optional: survive a Haier shutdown (future-proofing)

"What if Haier discontinues the service?" — a device you **already control keeps working**. The localKey is
stored on the AC; with no server to push a rotation, your key stays valid, and the integration drives the AC
locally forever. To be fully immune:

1. **Archive every AC's localKey now** (while the cloud is alive). Each AC has a **Local key** diagnostic
   sensor (disabled by default). Enable it on the device page → its state is the key and its attributes
   carry host + deviceId + version. It then rides along in your HA backups automatically. Keep those safe —
   the key grants ongoing local control.
2. **Onboard each AC via the config-flow `manual` path** (host + deviceId + key from the backup). Manual needs
   **zero cloud** — no login, no gateway — so nothing depends on Haier being up.
3. **Firewall the ACs** (§4) so the key never rotates.

That's it — those ACs are now Haier-independent. **The one thing this doesn't cover** is *factory-resetting* or
adding a *brand-new* AC after Haier is gone (a wiped device has no key and normally fetches one from the cloud).
Workarounds for that narrow case: run your **own** key-issuing server (a large, hardware-gated project) or,
as the guaranteed floor, flash **ESPHome** onto the module.

## Troubleshooting

- **HA log: "Requirements for haismart not found" / import errors.** The two libs landed in a different Python
  than HA's. Re-run `install-dev.sh` with `--python` pointing at HA's interpreter (Core/venv: the venv's
  `bin/python`; Docker: run inside the container).
- **"No decodable status" / entities unavailable right after adding.** Usually a **stale `localKey`** — it
  rotates server-side. The login/cloud paths auto-refetch it; the manual path will prompt a reauth (and raise
  a repair suggesting you add account creds so it self-heals next time).
- **Can't reach the AC.** Confirm HA and the AC are on the same subnet and `:56800` is open:
  `nc -z <ac-ip> 56800`. The integration finds the AC by **DHCP** (its MAC starts `AC:B7:22`) or the host you
  provide; if you blocked the AC's WAN (§4), make sure you left its **LAN** open.
- **Login rejected (retCode 30032).** Wrong region code or credentials — the region routes the account lookup
  (e.g. `66` for a Thailand account). `10001` = a missing field.

## Uninstall

Remove the integration in the UI, then delete `<config>/custom_components/haismart/`. (The helper libs pip-
installed into HA's env are harmless to leave; `pip uninstall haismart-hrdp haismart-extractor` removes them.)
