# Haismart Local — Haier air conditioners in Home Assistant, with no cloud

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Release](https://img.shields.io/github/v/release/enapt/haismart-local?color=green)](https://github.com/enapt/haismart-local/releases/latest)
[![Validate](https://img.shields.io/github/actions/workflow/status/enapt/haismart-local/validate.yml?branch=main&label=validate)](https://github.com/enapt/haismart-local/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/enapt/haismart-local)](LICENSE)

_Control your Haier air conditioner from Home Assistant entirely over your own network. You sign in
once so the integration can fetch your unit's key — after that it talks only to the AC, on your LAN._

<details>
<summary><b>Table of contents</b></summary>

- [Is my air conditioner supported?](#is-my-air-conditioner-supported)
- [What you get](#what-you-get)
- [Before you install](#before-you-install)
- [Installation](#installation)
- [Set up your air conditioner](#set-up-your-air-conditioner)
- [Automation examples](#automation-examples)
- [Going fully cloud-independent](#going-fully-cloud-independent)
- [Troubleshooting](#troubleshooting)
- [Before you open an issue](#before-you-open-an-issue)
- [Contributing](#contributing)
- [Credits](#credits)
- [How sign-in works](#how-sign-in-works)
- [Disclaimer](#disclaimer)

</details>

> [!IMPORTANT]
> Your Haier account is used **once**, during setup, to fetch your AC's local encryption key. From
> then on Home Assistant talks directly to the air conditioner over TCP port 56800 on your LAN.
> Reading state and sending commands never leave your network — and keep working if your internet
> does not.

## Is my air conditioner supported?

**The app you use is what matters, not the country you're in.** If your AC pairs with the
**Haier / Haismart** app (also branded *Haier U+* or *uHome*), you're in the right place. Despite
the "SE-Asia" label the platform carries internally, accounts registered well outside that region
work fine — this is used daily on an account registered outside South-East Asia.

| Your app | Supported here? | Use instead |
|---|---|---|
| **Haier / Haismart / Haier U+ / uHome** | ✅ **Yes** | — |
| hOn (mostly Europe) | ❌ No — these modules don't open port 56800 at all | [Andre0512/hon](https://github.com/Andre0512/hon) |
| Haier 智家 (mainland China) | ❌ No — different cloud | [banto6/haier](https://github.com/banto6/haier) |
| SmartHQ (US / GE Appliances) | ❌ No — different platform entirely | — |
| SmartAir2 / Smart Clima (older units) | ❌ No — same port, older unencrypted protocol | [oxystin/homebridge-haier-air-conditioner](https://github.com/oxystin/homebridge-haier-air-conditioner) |

**Confirmed working units** are listed in [`DEVICES.md`](DEVICES.md). Yours not there? It will very
likely still work — the integration builds itself from the model description your AC's own cloud
profile provides, rather than from hard-coded per-model tables. If something decodes oddly, that's a
[great issue to open](#before-you-open-an-issue), and usually a quick fix.

**Quick check:** if `nc -z <your-ac-ip> 56800` succeeds, the local protocol is listening.

## What you get

One device per air conditioner, with:

| Entity | What it does |
|---|---|
| **Climate** | Temperature setpoint, mode (cool / heat / dry / fan-only / auto), fan speed, swing, on/off |
| **Indoor temperature** | The AC's own room-temperature reading |
| **Outdoor temperature** | Outdoor probe, on units that have one |
| **Switches** | Strong, Quiet, Health, Sleep, Display light |
| **Eco** | Eco level, on models where it's confirmed |
| **Local key** *(diagnostic, off by default)* | Your unit's key, so it rides along in HA backups |

Which of these appear depends on your model — the integration only exposes controls it can actually
drive on your unit, rather than showing buttons that do nothing.

## Before you install

Worth knowing up front, so nothing surprises you:

- Home Assistant and the AC must be on the **same subnet**. There's no cloud relay to fall back on.
- The AC accepts **one local session at a time**, and each session is capped at about 17 seconds.
  Running another Haier local integration against the same unit will cause both to misbehave.
- Installing this **does not stop your AC talking to Haier**. It keeps its own cloud connection
  unless you firewall it — see [going fully cloud-independent](#going-fully-cloud-independent).
- Give the AC a **DHCP reservation**. If its IP moves, Home Assistant has to find it again.
- Social logins (Google / Facebook) have no password to sign in with. Create a throwaway
  email/password Haier account, **share the AC to it** in the app, and use that here — sharing grants
  the same local access as ownership.

## Installation

### Option 1 — HACS (recommended)

1. Make sure [HACS](https://hacs.xyz/) is installed.
1. Open this repository in HACS:\
   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=enapt&repository=haismart-local&category=integration)
1. Click **Download**, then **Download** again in the version dialog.
1. **Restart Home Assistant.** A custom integration's code is only loaded at startup — reloading the
   entry is not enough.

<details>
<summary>The button didn't work — add it by hand</summary>

1. Open **HACS** from the sidebar.
1. Three-dot menu, top right → **Custom repositories**.
1. Repository: `https://github.com/enapt/haismart-local`, type **Integration** → **Add**.
1. Search HACS for **Haismart** → **Download**.
1. Restart Home Assistant.

</details>

<details>
<summary>Option 2 — manual installation</summary>

1. Download the source of the [latest release](https://github.com/enapt/haismart-local/releases/latest).
1. Copy the `custom_components/haismart/` folder into your Home Assistant `config/custom_components/`.
1. Restart Home Assistant.

It's fully self-contained — no `pip install` step, the helper libraries are bundled.

</details>

## Set up your air conditioner

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=haismart)

Or: **Settings → Devices & Services → + Add Integration → Haismart**. If it isn't listed, hard-refresh
your browser (<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd>).

Then pick one of two paths:

**Sign in (recommended).** Enter your Haier account email (or phone) and password, and the country
your **account** was registered in. The integration lists your air conditioners, fetches the chosen
one's key automatically, and finds it on your network — you won't paste anything.

> The country field is the **phone dialling code of the country your Haier account was created in**
> — not where the AC is installed, and not necessarily where you live now. Getting it wrong is the
> single most common setup failure, because Haier's server reports it as "account not registered",
> which reads like a wrong password.

**Manual.** Host + device ID + local key, entered directly. Completely offline — no account needed.
Use this if you already have a key (from the *Local key* diagnostic sensor, or a backup).

## Automation examples

```yaml
# Pre-cool the living room before you get home
automation:
  - alias: "Pre-cool before arrival"
    triggers:
      - trigger: zone
        entity_id: person.me
        zone: zone.home
        event: enter
    actions:
      - action: climate.set_temperature
        target: { entity_id: climate.living_room_ac }
        data: { temperature: 23, hvac_mode: cool }
```

```yaml
# Quiet mode overnight
automation:
  - alias: "AC quiet at night"
    triggers:
      - trigger: time
        at: "22:30:00"
    actions:
      - action: switch.turn_on
        target: { entity_id: switch.living_room_ac_quiet }
```

## Going fully cloud-independent

Everything already runs locally after setup. The one remaining cloud dependency is that Haier's
server can **rotate** your unit's local key, which the integration then re-fetches.

If you'd rather your AC never phoned home at all:

1. **Archive the key first.** Enable the *Local key* diagnostic sensor on the device page — its state
   is the key, and its attributes carry the host, device ID and version. It then rides along in your
   Home Assistant backups automatically.
2. **Block the AC's internet access** at your router (keep LAN open — Home Assistant still needs port
   56800). Per-device WAN blocking by MAC is the reliable way; DNS blocking can be bypassed by
   hardcoded addresses.
3. The key can no longer rotate, so your stored key stays valid indefinitely. You can always re-add
   the unit later through the **Manual** path, with no cloud involved at all.

Full details, including the domain list: [`INSTALL.md`](INSTALL.md).

## Troubleshooting

<details>
<summary><b>"Sign-in failed" / "account not registered"</b></summary>

Almost always the **country code**. It's the phone dialling code of the country your Haier *account*
was registered in, which may not be where you live now or where the AC is. If you're certain it's
right, check whether your account is actually a Haier / Haismart one — hOn and Haier China accounts
live on entirely different servers and no country code will work.

</details>

<details>
<summary><b>"No decodable status from &lt;ip&gt;"</b></summary>

The AC answered and the connection is fine, but Home Assistant couldn't read the reply. Two causes:

- **Stale local key** — keys rotate server-side. If you signed in with your account, it re-fetches
  automatically; otherwise you'll be prompted to re-authenticate.
- **A report layout we don't know yet** — your model packs its status slightly differently. Recent
  versions decode what they can and say so explicitly instead of failing outright. Please
  [open an issue](#before-you-open-an-issue) with diagnostics; it's usually a one-line fix.

</details>

<details>
<summary><b>Entities are unavailable, or the AC dropped off</b></summary>

Check its IP hasn't changed (set a DHCP reservation), that nothing else is holding a local session
to the same unit, and that `nc -z <ac-ip> 56800` still succeeds.

</details>

<details>
<summary><b>Turn on debug logging</b></summary>

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.haismart: debug
    haismart_hrdp: debug
    haismart_extractor: debug
```

</details>

## Before you open an issue

This is a reverse-engineered local protocol, so a good report is worth a great deal:

1. Check the [Logs page](https://my.home-assistant.io/redirect/logs/) for warnings from `haismart`.
1. Enable debug logging (above) and reproduce the problem.
1. Search [existing issues](https://github.com/enapt/haismart-local/issues?q=is%3Aissue),
   including closed ones.
1. Download diagnostics: **Settings → [Devices & Services](https://my.home-assistant.io/redirect/integrations/)
   → Haismart → ⋮ → Download diagnostics**. Secrets are redacted; the raw status bytes it contains
   are exactly what's needed to diagnose a decode problem.
1. Include your **AC model number**, the Wi-Fi module if you know it, and the app you pair with.

**Adding support for a new model** is the most valuable contribution here, and it doesn't require
writing any code — see [`docs/new-model.md`](docs/new-model.md) for a short capture procedure.

## Contributing

Pull requests are genuinely welcome, whether or not you write Python:

- **Report a new model** — the capture procedure in [`docs/new-model.md`](docs/new-model.md) turns
  adding a model into a desk job. No hardware access needed on our side.
- **Translations** — the UI strings live in
  [`translations/`](packages/ha-haismart/custom_components/haismart/translations). Adding a language
  is a single JSON file.
- **Code** — see [`CONTRIBUTING.md`](CONTRIBUTING.md). It's a three-package monorepo; tests run with
  no hardware and no network.
- **Just using it and saying it worked** on a model not in [`DEVICES.md`](DEVICES.md) is a real
  contribution too.

Protocol details, if you want to dig in: [`PROTOCOL.md`](PROTOCOL.md).

## Credits

The local uSS/HRDP protocol here — the handshake, the AES/localKey biz-data layer, the status
decode and the grSetDAC control path — was reverse-engineered from scratch for this project.

Large parts of the multi-device support come from [**@darkdiamond**](https://github.com/darkdiamond),
developed in a fork and merged back here with history intact: support for a second report layout
(and graceful degradation on an unknown one), the digital-model enum derivation that makes any model
self-describe, the real product code, **heat mode confirmed on heat-capable hardware**, the
horizontal-swing axis, the sign-in country picker and recovery flows, localisation, and this repo's
CI. Thank you.

Standing on the shoulders of prior Haier reverse-engineering:

- [bstuff/haier-ac-remote](https://github.com/bstuff/haier-ac-remote) and
  [roeij/py-haier-ac-remote](https://github.com/roeij/py-haier-ac-remote) — early port-56800 work
- [KoalaBear84/HaierAC](https://github.com/KoalaBear84/HaierAC) — protocol logging, session behaviour
- [oxystin/homebridge-haier-air-conditioner](https://github.com/oxystin/homebridge-haier-air-conditioner)
  — SmartAir2 units and a valuable compatibility list
- [paveldn/haier-esphome](https://github.com/paveldn/haier-esphome) — the e++ frame layer
- [banto6/haier](https://github.com/banto6/haier) — the mainland-China cloud
- [Andre0512/hon](https://github.com/Andre0512/hon) / [pyhOn](https://github.com/Andre0512/pyhOn) —
  the hOn platform

And to everyone who opens an issue, reports a model, or stars the repo. ⭐

## How sign-in works

Setup uses **the app's own sign-in flow with your own account**: you enter your Haier credentials,
the integration signs a normal API request with the app-level identifiers (an `appId`/`appKey` pair
that is the same for every install of the Haismart app), and Haier returns your AC's local key. There
is no authentication bypass, no defeated protection and no per-user secret of anyone else's involved —
the same interoperability model that [`banto6/haier`](https://github.com/banto6/haier) uses for
Haier's mainland app and [pyhOn](https://github.com/Andre0512/pyhOn) /
[`hon`](https://github.com/Andre0512/hon) use for the hOn platform.

Those app-level identifiers ship as defaults so sign-in works out of the box. If you would rather
supply your own, every one of them is overridable by environment variable —
`HAISMART_APP_ID`, `HAISMART_APP_KEY`, `HAISMART_CLIENT_ID`, `HAISMART_APP_VERSION`.

Everything after setup is local: the protocol the AC speaks on port 56800 was worked out for this
project so a unit you own can be driven from your own network. That is the point of the exercise —
interoperability with your own hardware, not access to anything that isn't yours.

## Disclaimer

An independent community project, **not affiliated with, authorised, or endorsed by Haier**. "Haier",
"Haismart" and "Haier U+" are trademarks of their respective owners, used here only to identify the
hardware this software interoperates with.

Setup signs in to Haier's account API with **your own** credentials; nothing is bypassed. Sharing a
device to a secondary account may be governed by the app's terms of service, and compliance is your
responsibility. Provided as-is, without warranty, for use with hardware you own on your own network.

Licensed under [MIT](LICENSE).
