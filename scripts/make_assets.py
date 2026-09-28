#!/usr/bin/env python3
"""Generate the README artwork.

Every asset ships in a light and a dark variant, and both come from one
description here rather than from two hand-edited files that drift apart. Run
``python3 scripts/make_assets.py`` after changing anything below.

The diagrams are schematics. Where one carries a number, the number comes from
``python3 -m weir demo`` and is labelled as such; where a shape is illustrative,
it says so on the face of the diagram rather than in a caption someone will
crop off.
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "assets"

SANS = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, monospace"

# ``water`` paints graphical marks and needs 3:1; ``water_ink`` is the darker
# sibling used wherever the teal carries words, because small teal text on a
# light panel is 3.5:1 and fails WCAG AA at any size below 19px.
LIGHT = dict(
    name="light", ink="#0B2530", muted="#5B7180", panel="#F3F8F9",
    line="#C6D5DB", water="#0D9488", water_ink="#0F766E", warn="#C2410C",
    bad="#DC2626", chip="#E6F2F1",
)
DARK = dict(
    name="dark", ink="#E6EDF3", muted="#93A6B3", panel="#12181F",
    line="#2C3A45", water="#2DD4BF", water_ink="#2DD4BF", warn="#FB923C",
    bad="#F87171", chip="#10262A",
)
THEMES = (LIGHT, DARK)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, *, size=15, fill="#000", weight=400, anchor="start",
         family=SANS, opacity=1.0, spacing=None):
    sp = f' letter-spacing="{spacing}"' if spacing is not None else ""
    op = f' opacity="{opacity}"' if opacity != 1.0 else ""
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{sp}{op}>'
            f'{esc(s)}</text>')


def rect(x, y, w, h, *, fill="none", stroke="none", rx=0, sw=1, dash=None, opacity=1.0):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    op = f' opacity="{opacity}"' if opacity != 1.0 else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"{d}{op}/>')


def line(x1, y1, x2, y2, *, stroke="#000", sw=1, dash=None, cap="round", opacity=1.0):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    op = f' opacity="{opacity}"' if opacity != 1.0 else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" '
            f'stroke-width="{sw}" stroke-linecap="{cap}"{d}{op}/>')


def svg(w, h, body, *, title="", size=None) -> str:
    t = f"<title>{esc(title)}</title>" if title else ""
    ow, oh = (size, size) if size else (w, h)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{ow}" height="{oh}" role="img">{t}{body}</svg>')


# ---------------------------------------------------------------------------
# The mark: water cascading over a three-step weir.
#
# Three steps, because a weir is a staircase you can measure a river with, and
# because the architecture has three planes. The water keeps a constant 18px
# clearance over each tread and curves exactly over each riser - the shape is a
# nappe, which is the part of a real weir that does the measuring.
#
# Held as ops rather than as an SVG path string so that scripts/make_avatar.py
# can stamp the same geometry into a PNG without a second copy of the numbers.
# ---------------------------------------------------------------------------

STRUCT_OPS = [("M", 18, 42), ("L", 54, 42), ("L", 54, 70),
              ("L", 86, 70), ("L", 86, 98), ("L", 118, 98)]
WATER_OPS = [("M", 18, 24), ("L", 46, 24), ("C", 56, 24, 54, 52, 64, 52),
             ("L", 78, 52), ("C", 88, 52, 86, 80, 96, 80), ("L", 118, 80)]
STRUCT_SW, WATER_SW = 11, 9


def to_d(ops) -> str:
    return " ".join(op + " " + " ".join(f"{n:g}" for n in args) for op, *args in ops)


def mark(t: dict, sw_struct: int = STRUCT_SW, sw_water: int = WATER_SW) -> str:
    return (
        f'<path d="{to_d(STRUCT_OPS)}" fill="none" stroke="{t["ink"]}" '
        f'stroke-width="{sw_struct}" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="{to_d(WATER_OPS)}" fill="none" stroke="{t["water"]}" '
        f'stroke-width="{sw_water}" stroke-linecap="round"/>'
    )


def build_logo(t: dict) -> str:
    return svg(128, 128, mark(t), title="weir")


def build_banner(t: dict) -> str:
    b = [
        f'<g transform="translate(34 40) scale(0.70)">{mark(t)}</g>',
        text(148, 96, "weir", size=60, fill=t["ink"], weight=700, spacing="-1.5"),
        text(152, 126, "a router for an internet whose traffic is agents",
             size=17, fill=t["muted"]),
        line(150, 141, 620, 141, stroke=t["line"], sw=1),
        text(150, 163, "intent-addressed  ·  budget-conserving  ·  deadline-aware",
             size=12.5, fill=t["water_ink"], weight=600, spacing="0.6"),
    ]
    return svg(720, 190, "".join(b), title="weir")


# ---------------------------------------------------------------------------
# Pipeline: cheapest and most terminal check first.
# ---------------------------------------------------------------------------

STAGES = [
    ("1", "parse", 9, "a few hundred bytes of ASCII"),
    ("2", "attest", 12, "one keyed hash"),
    ("3", "loop / depth", 7, "header arithmetic, terminal"),
    ("4", "deadline", 6, "one comparison"),
    ("5", "terms", 10, "before the origin is contacted"),
    ("6", "route", 13, "capability lookup"),
    ("7", "budget", 17, "first contended lock"),
    ("8", "damper", 15, "admission + appointment"),
    ("9", "coalesce", 12, "may avoid the upstream entirely"),
    ("10", "forward", 565, "the only expensive step"),
    ("11", "settle", 11, "declared vs observed"),
    ("12", "receipt", 13, "recorded either way"),
]


def build_pipeline(t: dict) -> str:
    W, top, rh = 980, 96, 30
    b = [
        text(24, 40, "Pipeline order: cheapest and most terminal first",
             size=21, fill=t["ink"], weight=700),
        text(24, 64, "The expensive thing is not the forwarding, it is the upstream. "
                     "A refusal after that call has saved nothing.",
             size=14, fill=t["muted"]),
        rect(16, top - 12, W - 32, rh * 9 + 6, fill=t["panel"], rx=10),
        text(W - 28, top + 4, "stages 1–9  ·  no network  ·  sub-millisecond",
             size=12.5, fill=t["water_ink"], weight=650, anchor="end"),
    ]
    x0 = 214
    for i, (num, name, cost, note) in enumerate(STAGES):
        y = top + 22 + i * rh
        expensive = num == "10"
        col = t["warn"] if expensive else (t["muted"] if i > 9 else t["water"])
        b.append(text(44, y + 5, num, size=13, fill=t["muted"], anchor="end", family=MONO))
        b.append(text(58, y + 5, name, size=15, fill=t["ink"],
                      weight=700 if expensive else 500))
        b.append(rect(x0, y - 9, cost, 18, fill=col, rx=4,
                      opacity=1.0 if expensive else 0.85))
        b.append(text(x0 + cost + 12, y + 5, note, size=13,
                      fill=t["ink"] if expensive else t["muted"],
                      weight=650 if expensive else 400))
    foot = top + 22 + 12 * rh + 6
    b.append(line(16, foot, W - 16, foot, stroke=t["line"], sw=1, dash="3 4"))
    b.append(text(24, foot + 26,
                  "Bar widths are illustrative, and generous to stages 1–9. The real ratio "
                  "between parsing a header and one upstream inference call is far larger",
                  size=12.5, fill=t["muted"]))
    b.append(text(24, foot + 44, "than a page this wide can show.",
                  size=12.5, fill=t["muted"]))
    return svg(W, foot + 64, "".join(b), title="weir forwarding pipeline")


# ---------------------------------------------------------------------------
# Appointments: a rejection with no schedule vs a slot that was set aside.
# ---------------------------------------------------------------------------

BURSTY = [0, 2, 9, 4, 1, 7, 12, 3, 1, 8, 11, 2, 0, 6, 10, 3, 1, 9]
PACED = [3, 4, 4, 4, 3, 4, 4, 4, 4, 3, 4, 4, 4, 4, 3, 4, 4, 4]
CAPACITY = 4
UNIT = 7.2


def _lane(t, x0, y_base, data, colour, over_colour, bw=38, gap=8, unit=UNIT):
    out = []
    for i, v in enumerate(data):
        x = x0 + i * (bw + gap)
        if v == 0:
            continue
        h = v * unit
        out.append(rect(x, y_base - h, bw, h, fill=colour, rx=3, opacity=0.9))
        if v > CAPACITY:
            over_h = (v - CAPACITY) * unit
            out.append(rect(x, y_base - h, bw, over_h, fill=over_colour, rx=3))
    out.append(line(x0 - 10, y_base - CAPACITY * unit,
                    x0 + len(data) * (bw + gap), y_base - CAPACITY * unit,
                    stroke=t["ink"], sw=1.5, dash="6 5", opacity=0.75))
    out.append(line(x0 - 10, y_base, x0 + len(data) * (bw + gap), y_base,
                    stroke=t["line"], sw=1.5))
    return out


def build_appointments(t: dict) -> str:
    W = 980
    x0 = 56

    def caption(base, data):
        # sit the caption just clear of the tallest bar, so it labels its own
        # lane instead of landing in a fixed place that a taller lane collides with
        return base - max(data) * UNIT - 26

    b = [
        text(24, 40, "A refusal that carries an appointment, not an apology",
             size=21, fill=t["ink"], weight=700),
        text(24, 64, "Retry storms exist because a rejection carries no scheduling "
                     "information, so every caller has to guess when to come back.",
             size=14, fill=t["muted"]),
    ]

    y1 = 214
    c1 = caption(y1, BURSTY)
    b.append(text(24, c1, "HTTP 429", size=15, fill=t["bad"], weight=700))
    b.append(text(104, c1, "“try again later” — everyone guesses, and they collide",
                  size=14, fill=t["muted"]))
    b += _lane(t, x0, y1, BURSTY, t["muted"], t["bad"])
    b.append(text(x0 - 16, y1 - CAPACITY * UNIT + 4, "cap", size=11,
                  fill=t["ink"], anchor="end", family=MONO, opacity=0.8))

    y2 = 394
    c2 = caption(y2, PACED)
    b.append(text(24, c2, "weir", size=15, fill=t["water_ink"], weight=700))
    b.append(text(104, c2,
                  "a signed slot each — arriving early costs credits and cannot move it",
                  size=14, fill=t["muted"]))
    b += _lane(t, x0, y2, PACED, t["water"], t["water"])
    b.append(text(x0 - 16, y2 - CAPACITY * UNIT + 4, "cap", size=11,
                  fill=t["ink"], anchor="end", family=MONO, opacity=0.8))

    b.append(text(24, y2 + 40, "arrivals per interval  ·  dashed line is what the "
                               "upstream can actually serve", size=12.5, fill=t["muted"]))
    b.append(line(16, y2 + 58, W - 16, y2 + 58, stroke=t["line"], sw=1, dash="3 4"))
    b.append(text(24, y2 + 84,
                  "Shape is schematic. The measured figure, from the same 400 callers "
                  "in `weir demo storm`: 5,942 attempts against a classic gateway, "
                  "2,740 against weir.",
                  size=12.5, fill=t["muted"]))
    return svg(W, y2 + 104, "".join(b), title="appointments vs HTTP 429")


# ---------------------------------------------------------------------------
# Header comparison: one question vs four.
# ---------------------------------------------------------------------------

IPV4 = [("version / IHL", 0), ("total length", 0), ("identification", 0),
        ("flags / fragment offset", 0), ("TTL", 1), ("protocol", 0),
        ("header checksum", 0), ("source address", 0), ("destination address", 2)]

WIH = [
    ("who authorised this", ["principal", "attest"]),
    ("what is it trying to do", ["capability", "intent-digest"]),
    ("what may it spend", ["root", "budget", "declared-cost"]),
    ("when does the answer expire", ["deadline", "coupling"]),
]


def build_header(t: dict) -> str:
    W, H = 980, 384
    b = [
        text(24, 40, "What the router can decide is bounded by what the header carries",
             size=21, fill=t["ink"], weight=700),
    ]

    # left panel
    lx, ly, lw = 24, 68, 420
    b.append(rect(lx, ly, lw, 264, fill=t["panel"], stroke=t["line"], rx=12))
    b.append(text(lx + 20, ly + 32, "IPv4  ·  20 bytes", size=16,
                  fill=t["ink"], weight=700, family=MONO))
    for i, (nm, hot) in enumerate(IPV4):
        y = ly + 56 + i * 22
        col = t["ink"] if hot else t["muted"]
        if hot:
            b.append(rect(lx + 16, y - 12, lw - 32, 19, fill=t["chip"], rx=4))
        b.append(text(lx + 24, y + 2, nm, size=13, fill=col,
                      weight=700 if hot else 400, family=MONO))
    b.append(text(lx + 20, ly + 254, "one question:  where is this going?",
                  size=14, fill=t["muted"], weight=600))

    # right panel
    rx_, ry, rw = 500, 68, 456
    b.append(rect(rx_, ry, rw, 264, fill=t["panel"], stroke=t["water"], rx=12))
    b.append(text(rx_ + 20, ry + 32, "WIH-0", size=16, fill=t["ink"],
                  weight=700, family=MONO))
    y = ry + 58
    for label, fields in WIH:
        b.append(line(rx_ + 18, y - 10, rx_ + 18, y + 26, stroke=t["water"], sw=3))
        b.append(text(rx_ + 32, y + 2, label, size=14, fill=t["ink"], weight=650))
        b.append(text(rx_ + 32, y + 21, "  ".join(fields), size=12.5,
                      fill=t["water_ink"], family=MONO))
        y += 48
    b.append(text(rx_ + 20, ry + 254, "four questions — every mechanism follows from these",
                  size=14, fill=t["muted"], weight=600))

    b.append(text(24, H - 12,
                  "IPv4 can express reachability, a crude loop bound and a crude "
                  "priority. That was the right header for its traffic.",
                  size=12.5, fill=t["muted"]))
    return svg(W, H, "".join(b), title="IPv4 header vs the Weir Intent Header")


BUILDERS = {
    "logo": build_logo,
    "banner": build_banner,
    "pipeline": build_pipeline,
    "appointments": build_appointments,
    "header": build_header,
}


# ---------------------------------------------------------------------------
# Icons: one file each, no light/dark pair.
#
# These are the only assets that would rather not know which theme they are in.
# A mid teal that clears 3:1 against both the GitHub light page and the dark one
# (4.2:1 and 4.5:1) lets them sit inline in Markdown, where a <picture> element
# is not available, and they carry an intrinsic 24px so `![](icon.svg)` can be
# dropped into a sentence. Anything with words on it ships themed instead.
# ---------------------------------------------------------------------------

ICON = "#0E8A88"
ISW = 5


def _i_path(d: str, c: str, sw: int = ISW) -> str:
    return (f'<path d="{d}" fill="none" stroke="{c}" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


def _i_rect(x, y, w, h, c: str, rx=3, sw=ISW, fill="none") -> str:
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{c}" stroke-width="{sw}"/>')


def _i_line(x1, y1, x2, y2, c: str, sw: int = ISW) -> str:
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c}" '
            f'stroke-width="{sw}" stroke-linecap="round"/>')


def _i_dot(cx, cy, c: str, r=4.6) -> str:
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{c}"/>'


# Each glyph is the mechanism's own claim, not a generic symbol for it.
ICONS = {
    # a cycle that dies where it repeats
    "delegation": lambda c: (
        _i_path("M17 43 L32 22 L47 43 Z", c)
        + _i_dot(17, 43, c) + _i_dot(32, 22, c) + _i_dot(47, 43, c)
        + _i_path("M27.5 38.5 L36.5 47.5", c, 4.4) + _i_path("M36.5 38.5 L27.5 47.5", c, 4.4)
    ),
    # one grant, cut into the shares a fan-out divides it into
    "budget": lambda c: (
        _i_rect(20, 8, 24, 16, c, rx=3, fill=c)
        + _i_rect(20, 27, 24, 12, c, rx=3, fill=c)
        + _i_rect(20, 42, 24, 8, c, rx=3, fill=c)
    ),
    # one cell of a calendar that was set aside for you
    "appointment": lambda c: (
        _i_rect(10, 15, 44, 38, c, rx=6, sw=4.6)
        + _i_line(10, 27, 54, 27, c, 4.6)
        + _i_line(20, 8, 20, 21, c, 4.6) + _i_line(44, 8, 44, 21, c, 4.6)
        + _i_rect(36, 33, 12, 12, c, rx=2.5, fill=c)
    ),
    "deadline": lambda c: _i_path("M16 9 H48 L33 32 L48 55 H16 L31 32 Z", c),
    # a chain of records, each one bound to the last
    "receipts": lambda c: (
        _i_rect(4, 22, 17, 20, c, rx=4, sw=4.4)
        + _i_rect(24.5, 22, 17, 20, c, rx=4, sw=4.4)
        + _i_rect(45, 22, 15, 20, c, rx=4, sw=4.4)
    ),
    # a hundred phrasings arriving at one upstream call
    "coalesce": lambda c: (
        _i_path("M8 15 C28 15 34 27 48 32", c)
        + _i_path("M8 32 H48", c)
        + _i_path("M8 49 C28 49 34 37 48 32", c)
        + _i_path("M40 24 L48 32 L40 40", c)
    ),
    # a policy that is enforced, not requested
    "terms": lambda c: (
        _i_path("M32 7 L55 15 V31 C55 45 45 54 32 58 C19 54 9 45 9 31 V15 Z", c)
        + _i_path("M22 32 L29 39 L42 25", c, 4.6)
    ),
}

ICON_TITLES = {
    "delegation": "delegation path vector", "budget": "conserved budget",
    "appointment": "appointment", "deadline": "deadline",
    "receipts": "receipts", "coalesce": "intent-digest coalescing",
    "terms": "terms enforced at the hop",
}


def build_icon(kind: str) -> str:
    return svg(64, 64, ICONS[kind](ICON), title=ICON_TITLES[kind], size=24)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in BUILDERS.items():
        for t in THEMES:
            path = OUT / f"{name}-{t['name']}.svg"
            path.write_text(fn(t))
            print(f"  {path.relative_to(OUT.parent.parent)}  {len(path.read_text()):>6} bytes")
    for kind in ICONS:
        path = OUT / f"icon-{kind}.svg"
        path.write_text(build_icon(kind))
        print(f"  {path.relative_to(OUT.parent.parent)}  {len(path.read_text()):>6} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
