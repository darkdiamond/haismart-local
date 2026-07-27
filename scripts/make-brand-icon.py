#!/usr/bin/env python3
"""Generate the integration's brand icons.

Since Home Assistant 2026.3 custom integrations ship their brand images inside the component
(`custom_components/<domain>/brand/`) rather than in the `home-assistant/brands` repository, which
auto-closes custom-integration PRs. The HACS `brands` check looks for `icon.png` there, so having
these lets the validation workflow run with no ignored checks.

The artwork is deliberately generic: a wall-mounted split unit with airflow beneath it. It uses **no
Haier trademark and no Home Assistant branding** — the first because this is an unaffiliated
interoperability project for a vendor with a history of takedown notices, the second because the
brands guidelines forbid custom integrations implying they are official.

Kept as a script rather than committing only the PNGs, so the icons can be regenerated or adjusted
without guessing at how they were made.

    python3 scripts/make-brand-icon.py
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

# Cool teal -> blue. Reads as "cooling", and being a filled tile it keeps its contrast on both light
# and dark dashboards, so a separate dark_icon.png is not needed.
TOP = (34, 190, 205)
BOTTOM = (20, 118, 200)
GLYPH = (255, 255, 255, 255)

SS = 4  # supersample factor; PIL has no anti-aliased primitives, so draw big and downsample


def _gradient(size: int) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel(
            (0, y),
            tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)),
        )
    return grad.resize((size, size), Image.NEAREST)


def render(size: int) -> Image.Image:
    s = size * SS
    tile = _gradient(s).convert("RGBA")

    # rounded-square mask, matching the corner radius Home Assistant's own tiles use
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=255)
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    icon.paste(tile, (0, 0), mask)

    d = ImageDraw.Draw(icon)

    # indoor unit: a wide rounded body with a vent slot along its lower edge
    bw, bh = int(s * 0.62), int(s * 0.19)
    bx, by = (s - bw) // 2, int(s * 0.22)
    d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=int(bh * 0.42), fill=GLYPH)
    vent_h = max(2, int(bh * 0.16))
    vent_y = by + bh - int(bh * 0.34)
    # Punch the vent through the body so it reads as a slot rather than a painted line, using the
    # tile colour AT THAT ROW -- filling with the gradient's top colour left the slot visibly the
    # wrong hue against the background it sits on.
    vent_fill = tile.getpixel((s // 2, min(s - 1, vent_y + vent_h // 2)))[:3]
    d.rounded_rectangle(
        (bx + int(bw * 0.10), vent_y, bx + int(bw * 0.90), vent_y + vent_h),
        radius=vent_h // 2,
        fill=vent_fill + (255,),
    )

    # three airflow curves, shortening as they fall away from the unit
    stroke = max(2, int(s * 0.035))
    for span, drop, alpha in ((0.60, 0.14, 255), (0.44, 0.28, 205), (0.28, 0.42, 150)):
        w = int(s * span)
        x0 = (s - w) // 2
        y0 = by + bh + int(s * drop)
        # a shallow arc: the bottom of an ellipse wider than it is tall
        d.arc(
            (x0, y0 - int(s * 0.10), x0 + w, y0 + int(s * 0.10)),
            start=200,
            end=340,
            fill=(255, 255, 255, alpha),
            width=stroke,
        )

    return icon.resize((size, size), Image.LANCZOS)


def main() -> None:
    out = pathlib.Path(__file__).resolve().parents[1] / "packages" / "ha-haismart"
    out = out / "custom_components" / "haismart" / "brand"
    out.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        img = render(size)
        img.save(out / name, "PNG", optimize=True)
        print(f"  {out.relative_to(out.parents[4])}/{name}  {img.size[0]}x{img.size[1]}")


if __name__ == "__main__":
    main()
