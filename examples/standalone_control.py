#!/usr/bin/env python3
"""Standalone proof-of-concept: read and control a Haismart Haier AC WITHOUT Home Assistant.

``haismart-hrdp`` has no Home Assistant dependency, so the same local protocol the integration uses can
drive an AC from any Python program — a CLI, a cron job, a Node-RED ``exec`` node, an MQTT bridge, etc.

You need three things per AC (the same trio as the integration's "Manual" onboarding):
  * ``--host``       the AC's LAN IP
  * ``--device-id``  the Wi-Fi module's MAC, no separators (e.g. ``ACB722AABBCC``)
  * ``--local-key``  the per-device 32-hex key. If you don't have it, fetch it once with
                     ``haismart-extractor`` (account login -> MQTT-gateway localKey fetch); see the README.

Examples::

    python examples/standalone_control.py --host 192.168.1.50 --device-id ACB722AABBCC \
        --local-key 00112233445566778899aabbccddeeff                 # read + print status
    python examples/standalone_control.py ... --set-temp 24          # set target temperature, then read back
"""
from __future__ import annotations

import argparse
import asyncio

import haismart_hrdp as h

STATUS_LEN = 127  # the decoded status push is a fixed-width blob


def read_current_status(host: str, device_id: str, local_key: str) -> bytes:
    """Handshake, decrypt the AC's status pushes, and return the newest full status blob."""
    blobs = [b for b in h.read_status(host, device_id, local_key) if len(b) == STATUS_LEN]
    if not blobs:
        # Handshake worked but nothing decrypted -> the localKey is wrong or stale (it rotates
        # server-side). Probe the AC's current version key-free to confirm.
        version = h.probe_localkey_version(host, device_id)
        raise SystemExit(
            f"no decodable status: the localKey did not decrypt (AC is on localKey version {version}). "
            "The key rotates server-side — re-fetch the current one."
        )
    return blobs[-1]


async def set_temperature(host: str, device_id: str, local_key: str, target_c: int) -> bytes:
    """Set the target temperature via a grSetDAC group-set, seeded from the AC's own live status in the
    SAME session (read-modify-write) so every other setting is preserved. Returns the confirmed status."""
    def build(status_blob: bytes | None) -> bytes:
        if status_blob is None:
            raise SystemExit("the AC did not push a status baseline to seed the change")
        words = h.grsetdac_baseline_from_status(status_blob)
        words = h.set_grsetdac_field(words, "targetTemperature", target_c - 16)  # epp = Celsius - 16
        return h.grsetdac_op_frame(words)

    replies = await h.async_send_op(host, device_id, local_key, counter=1, build_frame=build)
    confirmed = [b for b in replies if len(b) == STATUS_LEN]
    return confirmed[-1] if confirmed else read_current_status(host, device_id, local_key)


def main() -> None:
    p = argparse.ArgumentParser(description="Read/control a Haismart Haier AC without Home Assistant.")
    p.add_argument("--host", required=True, help="AC LAN IP")
    p.add_argument("--device-id", required=True, help="Wi-Fi MAC, no separators (e.g. ACB722AABBCC)")
    p.add_argument("--local-key", required=True, help="per-device 32-hex localKey")
    p.add_argument("--type-id", default="AAC1UKZ01", help="product code for the status profile")
    p.add_argument("--set-temp", type=int, metavar="C", help="set target temperature (Celsius), then read back")
    args = p.parse_args()

    profile = h.profile_for(args.type_id)
    if args.set_temp is not None:
        print(f"setting target temperature to {args.set_temp} C ...")
        blob = asyncio.run(set_temperature(args.host, args.device_id, args.local_key, args.set_temp))
    else:
        blob = read_current_status(args.host, args.device_id, args.local_key)

    print("AC status:")
    for key, value in h.parse_full_status(blob, profile).items():
        print(f"  {key:22} {value}")


if __name__ == "__main__":
    main()
