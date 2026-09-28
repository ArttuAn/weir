#!/usr/bin/env python3
"""Rasterise the repository avatar from the mark in make_assets.

GitHub will not take an SVG for a repository avatar, so the same geometry that
make_assets.py writes as vectors is stamped here into a 512px PNG. There is no
second copy of the numbers: both files read STRUCT_OPS and WATER_OPS.

Needs pycairo. Without it this exits 0 and explains itself, because the README
artwork is vector-only and should never depend on a raster toolchain.

Usage:  python3 scripts/make_avatar.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import cairo
except ImportError:
    cairo = None

from make_assets import STRUCT_OPS, STRUCT_SW, WATER_OPS, WATER_SW

OUT = Path(__file__).resolve().parent.parent / ".github" / "avatar.png"

SIZE = 512
TILE = "#0F766E"     # the deep teal, which clears 4.5:1 against white
STRUCT = "#FFFFFF"   # 5.5:1 on the tile
WATER = "#9AF0E6"    # the nappe, kept legible at 16px

# The mark's inked box in its own 128-unit space, round caps included, and how
# much of the tile it should fill.
INK_CX, INK_CY, INK_W = 68.0, 61.5, 111.0
FILL = 0.78


def draw_ops(ctx, ops) -> None:
    for op, *a in ops:
        if op == "M":
            ctx.move_to(a[0], a[1])
        elif op == "L":
            ctx.line_to(a[0], a[1])
        elif op == "C":
            ctx.curve_to(*a)


def stroke(ctx, ops, colour: str, width: float) -> None:
    ctx.save()
    ctx.set_source_rgb(*_rgb(colour))
    ctx.set_line_width(width)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    draw_ops(ctx, ops)
    ctx.stroke()
    ctx.restore()


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def render() -> None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(*_rgb(TILE))
    ctx.paint()

    scale = (FILL * SIZE) / INK_W
    ctx.translate(SIZE / 2, SIZE / 2)
    ctx.scale(scale, scale)
    ctx.translate(-INK_CX, -INK_CY)

    stroke(ctx, STRUCT_OPS, STRUCT, STRUCT_SW)
    stroke(ctx, WATER_OPS, WATER, WATER_SW)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(OUT))


def main() -> int:
    if cairo is None:
        print("pycairo not installed; skipping the avatar PNG. "
              "The SVG artwork does not need it.")
        return 0
    render()
    print(f"  {OUT.relative_to(OUT.parent.parent.parent)}  {OUT.stat().st_size:>6} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
