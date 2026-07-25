# Adding support for a new model

If your air conditioner connects but some values look wrong — or Home Assistant reports
`no decodable status` — its status report probably packs fields slightly differently. Working that
out needs **no code and no protocol knowledge**, just three captures from you.

## Why three captures

The report is a fixed prefix followed by a block of control words and then read-only sensor bytes.
Where the sensors start depends on how many control words your model carries. Changing one setting at
a time and diffing the results pins that boundary exactly: only the bytes that changed can belong to
the setting you changed, and the rest fall out by elimination.

## What to send

For each of the three states below: set it **on the AC's own remote or the Haier app** (not from Home
Assistant), wait about 30 seconds so Home Assistant polls at least once, then download diagnostics —
**Settings → Devices & Services → Haismart → ⋮ → Download diagnostics**.

| # | State to set | Also note |
|---|---|---|
| 1 | **Off** | — |
| 2 | **Cool, 22 °C, fan low, swing off** | the room temperature the remote displays |
| 3 | **Fan-only, fan high, swing on** | — |

Then open a [new model report](https://github.com/darkdiamond/haismart-local/issues/new?template=new_model.yml)
and attach all three, plus:

- the **model number** from the sticker on the indoor unit
- the **Wi-Fi module** model, if it is printed on the unit or shown in the app
- which **app** you pair with (Haier / Haismart / Haier U+ / uHome)
- the room temperature the remote showed in state 2

## Is anything secret in there?

No. Diagnostics redacts your account tokens and your device's local key. It does include the raw
status bytes, which are just the sensor and setting values — the same numbers your remote shows — and
the device ID, which is the Wi-Fi module's MAC address. That is not a credential, and it is needed to
make sense of the capture.

## What happens next

The three captures are diffed to locate the control-word block and the sensor bytes. The room
temperature you noted confirms the indoor-temperature byte immediately, since it is stored as twice
the reading. Adding the layout is then usually a single table entry, and you will be asked to confirm
the result on your unit before it ships.
