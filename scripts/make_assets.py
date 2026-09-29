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
    name="light", bg="#FFFFFF", ink="#0B2530", muted="#5B7180", panel="#F3F8F9",
    line="#C6D5DB", water="#0D9488", water_ink="#0F766E", warn="#C2410C",
    bad="#DC2626", chip="#E6F2F1",
)
DARK = dict(
    name="dark", bg="#0D1117", ink="#E6EDF3", muted="#93A6B3", panel="#12181F",
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


# ---------------------------------------------------------------------------
# Topology: where the box sits. Same binary, two placements, different job.
# ---------------------------------------------------------------------------

def _node(t, x, y, w, h, title, sub, *, accent=False):
    col = t["water"] if accent else t["line"]
    out = [rect(x, y, w, h, fill=t["panel"], stroke=col, rx=10, sw=2 if accent else 1)]
    out.append(text(x + w / 2, y + 26, title, size=15, fill=t["ink"],
                    weight=700, anchor="middle"))
    for i, sline in enumerate(sub):
        out.append(text(x + w / 2, y + 48 + i * 17, sline, size=12,
                        fill=t["muted"], anchor="middle"))
    return out


def _arrow(t, x1, y, x2, *, label="", colour=None):
    c = colour or t["muted"]
    out = [line(x1, y, x2 - 9, y, stroke=c, sw=2),
           f'<path d="M {x2} {y} L {x2 - 10} {y - 5} L {x2 - 10} {y + 5} Z" fill="{c}"/>']
    if label:
        out.append(text((x1 + x2) / 2, y - 10, label, size=11.5,
                        fill=c, anchor="middle"))
    return out


def build_topology(t: dict) -> str:
    W, H = 980, 340
    y, bh = 96, 96
    b = [
        text(24, 40, "Where the box sits", size=21, fill=t["ink"], weight=700),
        text(24, 64, "Same binary in both places. Neither subsumes the other: the "
                     "provider cannot see your fan-out, and you cannot see their capacity.",
             size=13.5, fill=t["muted"]),
    ]
    b += _node(t, 24, y, 176, bh, "agent fleet",
               ["planners, tools,", "sub-agents"])
    b += _arrow(t, 200, y + bh / 2, 250)
    b += _node(t, 250, y, 196, bh, "weir · egress",
               ["budget conservation", "delegation loops", "fan-out width"], accent=True)
    b += _arrow(t, 446, y + bh / 2, 520, label="public internet")
    b += _node(t, 520, y, 196, bh, "weir · ingress",
               ["terms + admission", "coalescing", "human reserve"], accent=True)
    b += _arrow(t, 716, y + bh / 2, 780)
    b += _node(t, 780, y, 176, bh, "model servers",
               ["the scarce GPUs"])

    b.append(text(250, y + bh + 30, "sees a whole root's tree", size=12,
                  fill=t["water"], weight=650))
    b.append(text(520, y + bh + 30, "owns the scarce resource", size=12,
                  fill=t["water"], weight=650))
    b.append(line(24, y + bh + 52, W - 24, y + bh + 52, stroke=t["line"],
                  sw=1, dash="3 4"))
    b.append(text(24, y + bh + 78,
                  "A third placement, between Agent Autonomous Systems, needs the CAP "
                  "control plane — designed in SPEC-CAP-0.md, not built.",
                  size=12.5, fill=t["muted"]))
    return svg(W, H, "".join(b), title="weir deployment topology")


# ---------------------------------------------------------------------------
# Inside the box: the offload boundary falls where the state begins.
# ---------------------------------------------------------------------------

def build_internals(t: dict) -> str:
    W, H = 980, 396
    b = [
        text(24, 40, "Inside the box: the offload boundary is the state boundary",
             size=21, fill=t["ink"], weight=700),
        text(24, 64, "Stages were ordered cheapest-first for latency. That ordering "
                     "turns out to decide what silicon can take.", size=13.5,
             fill=t["muted"]),
    ]

    # offloadable prefix
    x, y, w, h = 24, 92, 440, 150
    b.append(rect(x, y, w, h, fill=t["panel"], stroke=t["water"], rx=12, sw=2,
                  dash="7 5"))
    b.append(text(x + 18, y + 28, "stages 1–5  ·  stateless prefix", size=15,
                  fill=t["ink"], weight=700))
    for i, sname in enumerate(["parse", "attest", "loop / depth", "deadline", "terms"]):
        cx = x + 22 + i * 84
        b.append(rect(cx, y + 44, 74, 30, fill=t["chip"], stroke=t["water"], rx=6))
        b.append(text(cx + 37, y + 64, sname, size=11.5, fill=t["ink"],
                      anchor="middle", weight=600))
    b.append(text(x + 18, y + 100, "pure functions of the header.", size=12.5,
                  fill=t["muted"]))
    b.append(text(x + 18, y + 120, "OFFLOADABLE — a DPU can drop a loop or an "
                                   "expired", size=12.5, fill=t["water"], weight=650))
    b.append(text(x + 18, y + 136, "request without waking the host.", size=12.5,
                  fill=t["water"], weight=650))

    # host-only remainder
    x2 = 516
    b.append(rect(x2, y, 440, h, fill=t["panel"], stroke=t["line"], rx=12))
    b.append(text(x2 + 18, y + 28, "stages 6–12  ·  mutable state", size=15,
                  fill=t["ink"], weight=700))
    for i, sname in enumerate(["route", "budget", "damper", "coalesce"]):
        cx = x2 + 22 + i * 104
        b.append(rect(cx, y + 44, 94, 30, fill=t["panel"], stroke=t["line"], rx=6))
        b.append(text(cx + 47, y + 64, sname, size=11.5, fill=t["ink"],
                      anchor="middle", weight=600))
    b.append(text(x2 + 18, y + 100, "contended, mutable, money-bearing.", size=12.5,
                  fill=t["muted"]))
    b.append(text(x2 + 18, y + 120, "HOST ONLY — not a tuning decision,", size=12.5,
                  fill=t["warn"], weight=650))
    b.append(text(x2 + 18, y + 136, "a correctness one.", size=12.5,
                  fill=t["warn"], weight=650))

    # state durability strip
    sy = 268
    b.append(text(24, sy, "what survives losing the node", size=14,
                  fill=t["ink"], weight=700))
    items = [("appointments", "signed bearer tokens — survive", t["water"]),
             ("slot cursor", "soft — rebuilds in one interval", t["muted"]),
             ("ledger", "HARD — must be checkpointed", t["bad"]),
             ("receipts", "only if shipped off-box", t["warn"])]
    for i, (nm, note, col) in enumerate(items):
        cx = 24 + i * 238
        b.append(rect(cx, sy + 14, 224, 54, fill=t["panel"], stroke=col, rx=8))
        b.append(text(cx + 14, sy + 36, nm, size=13, fill=t["ink"],
                      weight=700, family=MONO))
        b.append(text(cx + 14, sy + 56, note, size=11.5, fill=col))

    b.append(text(24, H - 12,
                  "Measured state sizes and the dimensioning they imply: "
                  "docs/PHYSICAL.md §4, from scripts/bench.py.",
                  size=12.5, fill=t["muted"]))
    return svg(W, H, "".join(b), title="weir internals and offload boundary")



# ===========================================================================
# Patent-style figures.
#
# Drawing *convention*, not legal status: nothing here is filed, and the README
# says so. The convention is used because it is unusually demanding - every part
# carries a reference numeral, numerals stay consistent across sheets, and every
# branch of the method has to terminate somewhere. Drawing it this way is a test
# of whether the design is actually specified or merely described.
#
# Monochrome line art in the patent tradition, with one concession: the ink
# colour follows the theme, so the dark-mode sheet is legible. A pure black
# plate on a dark README is a plate nobody can read.
# ===========================================================================

PT_THIN, PT_MED, PT_THICK = 1.0, 1.5, 2.1


def p_rect(x, y, w, h, ink, *, rx=0, sw=PT_MED, fill="none", dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{ink}" stroke-width="{sw}"{d}/>')


def p_txt(x, y, sgn, ink, *, size=11, anchor="start", weight=500, family=SANS,
          spacing=None, opacity=1.0):
    return text(x, y, sgn, size=size, fill=ink, weight=weight, anchor=anchor,
                family=family, spacing=spacing, opacity=opacity)


def p_lead(pts, ink, *, sw=PT_THIN, dot=True):
    """A lead line: numeral to part, with the small terminal dot patents use."""
    d = " ".join(("M" if i == 0 else "L") + f" {x} {y}" for i, (x, y) in enumerate(pts))
    out = [f'<path d="{d}" fill="none" stroke="{ink}" stroke-width="{sw}"/>']
    if dot:
        ex, ey = pts[-1]
        out.append(f'<circle cx="{ex}" cy="{ey}" r="2.4" fill="{ink}"/>')
    return "".join(out)


def p_ref(nx, ny, target, n, ink, *, size=12, anchor="middle", via=None):
    """Reference numeral plus its lead line.

    The lead leaves the numeral from whichever side faces the part, so it never
    strikes through its own digits. Getting this wrong is the single most
    obvious tell that a plate was not drawn by someone who draws plates.
    """
    tx, ty = target
    start = (nx, ny - 13) if ty < ny else (nx, ny + 6)
    pts = [start] + ([via] if via else []) + [(tx, ty)]
    return p_lead(pts, ink) + p_txt(nx, ny, str(n), ink, size=size, anchor=anchor,
                                    weight=700, family=MONO)


def p_arrow(x1, y1, x2, y2, ink, *, sw=PT_MED, head=8):
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    bx, by = x2 - head * math.cos(ang), y2 - head * math.sin(ang)
    px, py = -math.sin(ang) * head * 0.42, math.cos(ang) * head * 0.42
    return (f'<line x1="{x1}" y1="{y1}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{ink}" '
            f'stroke-width="{sw}"/>'
            f'<path d="M {x2} {y2} L {bx + px:.1f} {by + py:.1f} '
            f'L {bx - px:.1f} {by - py:.1f} Z" fill="{ink}"/>')


def p_fig(x, y, label, ink, *, sub=""):
    out = p_txt(x, y, label, ink, size=15, weight=700, spacing="1.2")
    if sub:
        out += p_txt(x + 82, y, sub, ink, size=11.5, weight=500)
    return out


def p_cyl(x, y, w, h, ink, label, *, sw=PT_MED):
    """A data store, drawn the way patents draw one."""
    ry = 9
    return (f'<path d="M {x} {y + ry} a {w / 2} {ry} 0 0 1 {w} 0 v {h - 2 * ry} '
            f'a {w / 2} {ry} 0 0 1 {-w} 0 Z" fill="none" stroke="{ink}" '
            f'stroke-width="{sw}"/>'
            f'<path d="M {x} {y + ry} a {w / 2} {ry} 0 0 0 {w} 0" fill="none" '
            f'stroke="{ink}" stroke-width="{sw}"/>'
            + p_txt(x + w / 2, y + h / 2 + 8, label, ink, size=10, anchor="middle"))


def p_diamond(cx, cy, w, h, ink, lines, *, sw=PT_MED):
    out = [f'<path d="M {cx} {cy - h / 2} L {cx + w / 2} {cy} L {cx} {cy + h / 2} '
           f'L {cx - w / 2} {cy} Z" fill="none" stroke="{ink}" stroke-width="{sw}"/>']
    n = len(lines)
    for i, ln in enumerate(lines):
        out.append(p_txt(cx, cy + 4 + (i - (n - 1) / 2) * 12, ln, ink,
                         size=9.5, anchor="middle"))
    return "".join(out)


def p_step(x, y, w, h, ink, lines, *, rx=0, sw=PT_MED):
    out = [p_rect(x, y, w, h, ink, rx=rx, sw=sw)]
    n = len(lines)
    for i, ln in enumerate(lines):
        out.append(p_txt(x + w / 2, y + h / 2 + 4 + (i - (n - 1) / 2) * 12, ln, ink,
                         size=10, anchor="middle"))
    return "".join(out)


def p_sheet(t, n, total, w, h, body, title):
    ink = t["ink"]
    frame = (p_rect(14, 14, w - 28, h - 28, ink, sw=PT_THIN, fill="none")
             + p_txt(w - 26, 36, f"SHEET {n} OF {total}", ink, size=10.5,
                     anchor="end", weight=700, family=MONO, spacing="1")
             + p_txt(26, 36, "WEIR  —  INTENT-ADDRESSED FORWARDING APPARATUS", ink,
                     size=10.5, weight=700, family=MONO, spacing="1")
             + line(14, 46, w - 14, 46, stroke=ink, sw=PT_THIN))
    note = p_txt(26, h - 24,
                 "Patent drawing convention. Not a filing, not a granted patent, "
                 "no application pending.", ink, size=9.5, opacity=0.75)
    return svg(w, h, frame + body + note, title=title)


# --- FIG. 1  system topology ----------------------------------------------

def _fig1(t, oy):
    ink = t["ink"]
    b = [p_fig(30, oy, "FIG. 1", ink, sub="SYSTEM OVERVIEW")]
    y, bh = oy + 34, 84

    # 100 agent fleet
    b.append(p_rect(34, y, 150, bh, ink, sw=PT_THICK))
    b.append(p_txt(109, y + 22, "AGENT FLEET", ink, size=10.5, anchor="middle",
                   weight=700))
    for i in range(3):
        cy = y + 40 + i * 15
        b.append(p_rect(58, cy - 6, 102, 12, ink, sw=PT_THIN))
    b.append(p_ref(109, y - 10, (109, y), 100, ink))
    b.append(p_ref(44, y + bh + 22, (70, y + 48), 102, ink))

    b.append(p_arrow(184, y + bh / 2, 232, y + bh / 2, ink))
    b.append(p_ref(208, y + bh / 2 - 16, (208, y + bh / 2 - 2), 104, ink))

    # 200 egress apparatus
    b.append(p_rect(232, y, 158, bh, ink, sw=PT_THICK))
    b.append(p_txt(311, y + 24, "WEIR APPARATUS", ink, size=10.5, anchor="middle",
                   weight=700))
    b.append(p_txt(311, y + 40, "(EGRESS)", ink, size=9.5, anchor="middle"))
    b.append(p_txt(311, y + 60, "BUDGET · LOOPS · WIDTH", ink, size=8.5,
                   anchor="middle"))
    b.append(p_ref(311, y - 10, (311, y), 200, ink))

    b.append(p_arrow(390, y + bh / 2, 438, y + bh / 2, ink))

    # 300 network cloud
    cx, cy = 512, y + bh / 2
    b.append(f'<path d="M {cx - 56} {cy + 14} a 20 20 0 0 1 2 -38 a 26 26 0 0 1 46 -16 '
             f'a 24 24 0 0 1 42 12 a 19 19 0 0 1 0 42 Z" fill="none" stroke="{ink}" '
             f'stroke-width="{PT_MED}"/>')
    b.append(p_txt(cx + 6, cy + 4, "NETWORK", ink, size=9.5, anchor="middle"))
    b.append(p_ref(cx + 6, y - 10, (cx + 6, cy - 26), 300, ink))

    b.append(p_arrow(590, y + bh / 2, 638, y + bh / 2, ink))

    # 400 ingress apparatus
    b.append(p_rect(638, y, 158, bh, ink, sw=PT_THICK))
    b.append(p_txt(717, y + 24, "WEIR APPARATUS", ink, size=10.5, anchor="middle",
                   weight=700))
    b.append(p_txt(717, y + 40, "(INGRESS)", ink, size=9.5, anchor="middle"))
    b.append(p_txt(717, y + 60, "TERMS · ADMISSION", ink, size=8.5, anchor="middle"))
    b.append(p_ref(717, y - 10, (717, y), 400, ink))

    b.append(p_arrow(796, y + bh / 2, 844, y + bh / 2, ink))

    # 500 upstream
    b.append(p_rect(844, y, 108, bh, ink, sw=PT_THICK))
    b.append(p_txt(898, y + 30, "INFERENCE", ink, size=10.5, anchor="middle",
                   weight=700))
    b.append(p_txt(898, y + 46, "SERVERS", ink, size=10.5, anchor="middle",
                   weight=700))
    b.append(p_ref(898, y - 10, (898, y), 500, ink))
    return "".join(b), y + bh + 40


# --- FIG. 2  the apparatus -------------------------------------------------

def _fig2(t, oy):
    ink = t["ink"]
    b = [p_fig(30, oy, "FIG. 2", ink, sub="APPARATUS — FRONT ELEVATION AND PLAN VIEW")]

    # front elevation
    y = oy + 30
    b.append(p_txt(34, y + 10, "FRONT ELEVATION", ink, size=9, weight=700,
                   family=MONO, spacing="1"))
    fy, fh = y + 20, 56
    b.append(p_rect(34, fy, 884, fh, ink, sw=PT_THICK))
    # rack ears
    for ex in (34, 902):
        b.append(p_rect(ex - 16, fy + 6, 16, fh - 12, ink, sw=PT_MED))
        for hy in (fy + 16, fy + fh - 20):
            b.append(f'<circle cx="{ex - 8}" cy="{hy}" r="3.4" fill="none" '
                     f'stroke="{ink}" stroke-width="{PT_THIN}"/>')
    b.append(p_ref(26, fy + fh + 20, (24, fy + fh - 10), 202, ink))
    # status LEDs
    for i in range(4):
        b.append(f'<circle cx="{58 + i * 16}" cy="{fy + 28}" r="4" fill="none" '
                 f'stroke="{ink}" stroke-width="{PT_THIN}"/>')
    b.append(p_ref(70, fy + fh + 20, (70, fy + 34), 204, ink))
    # ports
    for i in range(2):
        b.append(p_rect(360 + i * 62, fy + 16, 46, 24, ink, sw=PT_MED))
        b.append(p_rect(366 + i * 62, fy + 22, 34, 12, ink, sw=PT_THIN))
    b.append(p_ref(391, fy + fh + 20, (391, fy + 42), 206, ink))
    b.append(p_rect(500, fy + 20, 34, 16, ink, sw=PT_MED))
    b.append(p_ref(517, fy + fh + 20, (517, fy + 38), 208, ink))
    # PSUs
    for i in range(2):
        b.append(p_rect(742 + i * 84, fy + 12, 74, 32, ink, sw=PT_MED))
        b.append(f'<circle cx="{779 + i * 84}" cy="{fy + 28}" r="10" fill="none" '
                 f'stroke="{ink}" stroke-width="{PT_THIN}"/>')
    b.append(p_ref(863, fy - 8, (863, fy + 12), 260, ink))

    # plan view (cover removed)
    py = fy + fh + 44
    b.append(p_txt(34, py, "PLAN VIEW — COVER REMOVED", ink, size=9, weight=700,
                   family=MONO, spacing="1"))
    py += 12
    ph = 232
    b.append(p_rect(34, py, 884, ph, ink, sw=PT_THICK))

    def blk(x, y_, w, h, num, lines, numpos, leadto, *, sw=PT_MED, dash=None):
        out = [p_rect(x, y_, w, h, ink, sw=sw, dash=dash)]
        n = len(lines)
        for i, ln in enumerate(lines):
            out.append(p_txt(x + w / 2, y_ + h / 2 + 4 + (i - (n - 1) / 2) * 11, ln,
                             ink, size=9, anchor="middle"))
        out.append(p_ref(numpos[0], numpos[1], leadto, num, ink))
        return "".join(out)

    b.append(blk(56, py + 22, 150, 74, 210,
                 ["NETWORK INTERFACE", "WITH TLS OFFLOAD", "(DPU / SmartNIC)"],
                 (131, py + 8), (131, py + 22)))
    b.append(blk(72, py + 104, 118, 40, 212,
                 ["STATELESS-PREFIX", "CLASSIFIER"], (131, py + 164),
                 (131, py + 144), dash="5 4"))

    b.append(blk(236, py + 40, 172, 120, 220,
                 ["HOST PROCESSOR", "COMPLEX", "", "PIPELINE STAGES", "6 – 12"],
                 (322, py + 24), (322, py + 40)))

    b.append(blk(438, py + 40, 124, 56, 230,
                 ["MAIN MEMORY", "(ROOT LEDGER)"], (500, py + 24), (500, py + 40)))
    b.append(blk(438, py + 108, 124, 52, 240,
                 ["NVMe RECEIPT", "SPOOL"], (500, py + 178), (500, py + 160)))

    b.append(blk(592, py + 40, 104, 44, 250, ["TPM / HSM"], (644, py + 24),
                 (644, py + 40)))
    b.append(blk(592, py + 100, 104, 60, 280, ["PCIe", "BACKPLANE"],
                 (644, py + 178), (644, py + 160)))

    for i in range(2):
        b.append(p_rect(726 + i * 84, py + 40, 74, 120, ink, sw=PT_MED))
        b.append(p_txt(763 + i * 84, py + 96, "PSU", ink, size=9, anchor="middle"))
        b.append(p_txt(763 + i * 84, py + 110, f"{i + 1} OF 2", ink, size=8,
                       anchor="middle"))
    b.append(p_ref(847, py + 178, (847, py + 160), 262, ink))

    # fans
    for i in range(4):
        fx = 60 + i * 38
        b.append(f'<circle cx="{fx}" cy="{py + 200}" r="13" fill="none" '
                 f'stroke="{ink}" stroke-width="{PT_THIN}"/>')
        b.append(f'<circle cx="{fx}" cy="{py + 200}" r="4" fill="none" '
                 f'stroke="{ink}" stroke-width="{PT_THIN}"/>')
    b.append(p_ref(36, py + 224, (60, py + 200), 270, ink))

    # interconnect lines
    b.append(line(206, py + 59, 236, py + 59, stroke=ink, sw=PT_MED))
    b.append(line(408, py + 68, 438, py + 68, stroke=ink, sw=PT_MED))
    b.append(line(408, py + 130, 438, py + 130, stroke=ink, sw=PT_MED))
    b.append(line(562, py + 62, 592, py + 62, stroke=ink, sw=PT_MED))
    b.append(line(562, py + 130, 592, py + 130, stroke=ink, sw=PT_MED))
    return "".join(b), py + ph + 34


def build_patent1(t: dict) -> str:
    W = 980
    f1, y1 = _fig1(t, 78)
    f2, y2 = _fig2(t, y1)
    return p_sheet(t, 1, 3, W, y2 + 24, f1 + f2,
                   "Patent-style figures, sheet 1: system overview and apparatus")



# --- FIG. 3  functional block diagram, offload boundary --------------------

def _fig3(t, oy):
    ink = t["ink"]
    b = [p_fig(30, oy, "FIG. 3", ink, sub="FUNCTIONAL BLOCK DIAGRAM")]

    # interface band with the stateless prefix
    ny = oy + 46
    by = oy + 56
    b.append(p_rect(40, by, 900, 84, ink, sw=PT_THICK))
    b.append(p_txt(52, by + 34, "NETWORK", ink, size=9, weight=700))
    b.append(p_txt(52, by + 48, "INTERFACE", ink, size=9, weight=700))
    b.append(p_txt(52, by + 62, "TLS OFFLOAD", ink, size=9, weight=700))
    b.append(p_ref(90, oy + 30, (90, by), 210, ink))

    b.append(p_rect(200, by + 10, 724, 64, ink, sw=PT_MED, dash="6 4"))
    b.append(p_ref(176, by + 82, (200, by + 60), 212, ink))
    prefix = [("PARSE", 302), ("VERIFY", 304), ("LOOP /", 306),
              ("DEADLINE", 308), ("TERMS", 310)]
    sub2 = ["HEADER", "ATTESTATION", "DEPTH", "CHECK", "CHECK"]
    for i, ((nm, num), s2) in enumerate(zip(prefix, sub2)):
        x = 211 + i * 142
        b.append(p_rect(x, by + 22, 134, 40, ink, sw=PT_MED))
        b.append(p_txt(x + 67, by + 38, nm, ink, size=9.5, anchor="middle",
                       weight=600))
        b.append(p_txt(x + 67, by + 52, s2, ink, size=8.5, anchor="middle"))
        b.append(p_ref(x + 67, ny - 10, (x + 67, by + 22), num, ink))

    # the boundary itself
    ly = by + 108
    b.append(line(40, ly, 940, ly, stroke=ink, sw=PT_MED, dash="9 6"))
    b.append(p_txt(48, ly - 8, "OFFLOAD BOUNDARY — NO SHARED MUTABLE STATE ABOVE",
                   ink, size=9, weight=700, family=MONO, spacing="0.6"))
    b.append(p_ref(906, ly - 14, (906, ly), 214, ink))

    # host band
    hy = ly + 38
    b.append(p_rect(40, hy, 900, 124, ink, sw=PT_THICK))
    b.append(p_txt(52, hy + 18, "HOST PROCESSOR COMPLEX", ink, size=9,
                   weight=700, family=MONO, spacing="0.6"))
    b.append(p_ref(60, hy - 14, (60, hy), 220, ink))
    host = [("ROUTE", "SELECT", 312), ("LEDGER", "DEBIT", 314),
            ("ADMISSION", "CONTROL", 316), ("INTENT", "COALESCE", 318),
            ("FORWARD", "UPSTREAM", 320), ("SETTLE", "OBSERVED", 322),
            ("RECEIPT", "WRITER", 324)]
    for i, (nm, s2, num) in enumerate(host):
        x = 60 + i * 124
        b.append(p_rect(x, hy + 30, 116, 48, ink, sw=PT_MED))
        b.append(p_txt(x + 58, hy + 50, nm, ink, size=9.5, anchor="middle",
                       weight=600))
        b.append(p_txt(x + 58, hy + 64, s2, ink, size=8.5, anchor="middle"))
        b.append(p_ref(x + 58, hy + 106, (x + 58, hy + 78), num, ink))
        if i:
            b.append(p_arrow(x - 8, hy + 54, x, hy + 54, ink, sw=PT_THIN, head=6))

    # persistent stores
    sy = hy + 156
    stores = [("ROOT LEDGER", 330, 118), ("KEY STORE (TPM)", 332, 366),
              ("INTENT CACHE", 334, 614), ("RECEIPT CHAIN", 336, 862)]
    for nm, num, cx in stores:
        b.append(p_cyl(cx - 75, sy, 150, 62, ink, nm))
        b.append(p_ref(cx, sy + 88, (cx, sy + 62), num, ink))
    # tie stores to the stages that own them
    for cx, sx in ((118, 60 + 1 * 124 + 100), (366, 60 + 2 * 124 + 100),
                   (614, 60 + 3 * 124 + 100), (862, 60 + 6 * 124 + 100)):
        b.append(line(cx, sy, cx, sy - 14, stroke=ink, sw=PT_THIN, dash="4 3"))
        b.append(line(cx, sy - 14, sx, sy - 14, stroke=ink, sw=PT_THIN, dash="4 3"))
        b.append(line(sx, sy - 14, sx, hy + 78, stroke=ink, sw=PT_THIN, dash="4 3"))
    return "".join(b), sy + 104


# --- FIG. 4  header format -------------------------------------------------

def _fig4(t, oy):
    ink = t["ink"]
    b = [p_fig(30, oy, "FIG. 4", ink, sub="INTENT HEADER FORMAT (WIH-0, BINARY)")]
    gx, gw = 150, 700
    y = oy + 46
    # bit ruler
    for bit in (0, 8, 16, 24, 32):
        x = gx + gw * bit / 32
        b.append(line(x, y + 8, x, y + 16, stroke=ink, sw=PT_THIN))
        b.append(p_txt(x, y + 4, str(bit if bit < 32 else 31), ink, size=8.5,
                       anchor="middle", family=MONO))
    b.append(p_txt(gx - 10, y + 4, "BIT", ink, size=8.5, anchor="end",
                   family=MONO, weight=700))

    rows = [
        ([("VER", 8), ("FLAGS", 8), ("DEPTH", 8), ("PATH LEN", 8)], 602),
        ([("ROOT IDENTIFIER  (128)", 32)], 604),
        ([("DEADLINE, MILLISECONDS  (64)", 32)], 606),
        ([("BUDGET  (32)", 16), ("DECLARED COST  (32)", 16)], 608),
        ([("CAPABILITY IDENTIFIER  (32)", 32)], 610),
        ([("PRINCIPAL IDENTIFIER  (128)", 32)], 612),
        ([("INTENT DIGEST  (128)", 32)], 614),
        ([("DELEGATION PATH VECTOR  (64 x n)", 32)], 616),
        ([("ATTESTATION  (128 OR 512)", 32)], 618),
    ]
    ry, rh = y + 22, 32
    for cells, num in rows:
        cx = gx
        for nm, span in cells:
            w = gw * span / 32
            b.append(p_rect(cx, ry, w, rh, ink, sw=PT_MED))
            b.append(p_txt(cx + w / 2, ry + rh / 2 + 4, nm, ink, size=9,
                           anchor="middle", family=MONO, weight=600))
            cx += w
        b.append(p_ref(gx + gw + 46, ry + rh / 2 + 4, (gx + gw, ry + rh / 2),
                       num, ink))
        ry += rh

    b.append(line(gx, ry + 10, gx + gw, ry + 10, stroke=ink, sw=PT_THIN, dash="4 3"))
    b.append(p_txt(gx, ry + 28,
                   "FIELDS 602–610 CARRY EVERY DROP DECISION. A CONFORMING "
                   "APPARATUS MAY REFUSE ON THE FIRST 24 OCTETS ALONE.",
                   ink, size=8.5, family=MONO, spacing="0.4"))
    return "".join(b), ry + 44


def build_patent2(t: dict) -> str:
    W = 980
    f3, y1 = _fig3(t, 78)
    f4, y2 = _fig4(t, y1 + 16)
    return p_sheet(t, 2, 3, W, y2 + 24, f3 + f4,
                   "Patent-style figures, sheet 2: block diagram and header format")



# --- FIG. 5  method of forwarding ------------------------------------------

def _fig5(t, oy):
    ink = t["ink"]
    b = [p_fig(30, oy, "FIG. 5", ink, sub="METHOD OF FORWARDING")]
    cx, bw = 172, 232          # main column
    rx, rw = 386, 140          # refusal column
    y = oy + 40
    step_h, gap = 36, 26

    def centre(yy):
        return yy + step_h / 2

    def refusal(yy, num, lines, label):
        """A terminal refusal, branching right off the main column."""
        out = [p_rect(rx, yy, rw, step_h, ink, sw=PT_MED)]
        n = len(lines)
        for i, ln in enumerate(lines):
            out.append(p_txt(rx + rw / 2, centre(yy) + 4 + (i - (n - 1) / 2) * 11,
                             ln, ink, size=8.5, anchor="middle"))
        out.append(p_arrow(cx + bw / 2 + 56, centre(yy), rx, centre(yy), ink,
                           sw=PT_THIN, head=6))
        out.append(p_txt(cx + bw / 2 + 62, centre(yy) - 6, label, ink, size=8,
                         family=MONO))
        out.append(p_ref(rx + rw + 32, centre(yy) + 4, (rx + rw, centre(yy)),
                         num, ink))
        return "".join(out)

    seq = [
        ("step", ["RECEIVE REQUEST"], 700, None, None),
        ("step", ["PARSE INTENT HEADER"], 702, None, None),
        ("test", ["WELL", "FORMED?"], 704, ("NO", 750, ["REFUSE", "MALFORMED"]), None),
        ("test", ["ATTESTATION", "VALID?"], 706, ("NO", 752, ["REFUSE", "BAD ATTESTATION"]), None),
        ("test", ["SELF IN PATH", "VECTOR?"], 708, ("YES", 754, ["REFUSE", "DELEGATION LOOP"]), None),
        ("test", ["DEPTH = 0 OR", "DEADLINE PAST?"], 710, ("YES", 756, ["REFUSE", "EXPIRED"]), None),
        ("test", ["TERMS", "PERMIT?"], 712, ("NO", 758, ["REFUSE", "TERMS DENIED"]), None),
        ("step", ["SELECT ROUTE BY", "CAPABILITY"], 714, None, None),
        ("test", ["ROOT BUDGET", "SUFFICIENT?"], 716, ("NO", 760, ["REFUSE", "BUDGET EXHAUSTED"]), None),
        ("test", ["CAPACITY", "AVAILABLE?"], 718, ("NO", 762, ["ISSUE APPOINTMENT", "CHARGE REFUSAL"]), None),
        ("test", ["IDENTICAL INTENT", "IN FLIGHT?"], 720, ("YES", 764, ["JOIN, RETURN", "SHARED RESULT"]), None),
        ("step", ["FORWARD TO UPSTREAM"], 722, None, None),
        ("step", ["SETTLE OBSERVED COST"], 724, None, None),
        ("step", ["APPEND RECEIPT"], 726, None, None),
        ("step", ["RETURN RESPONSE"], 728, None, None),
    ]

    prev_bottom = None
    for kind, lines, num, branch, _ in seq:
        h = step_h if kind == "step" else 48
        if kind == "test":
            b.append(p_diamond(cx, y + h / 2, bw, h, ink, lines))
            nx = cx - bw / 2 - 30
        else:
            b.append(p_step(cx - bw / 2, y, bw, h, ink, lines,
                            rx=step_h / 2 if num in (700, 728) else 0))
            nx = cx - bw / 2 - 30
        b.append(p_ref(nx, y + h / 2 + 4, (cx - bw / 2, y + h / 2), num, ink))
        if prev_bottom is not None:
            b.append(p_arrow(cx, prev_bottom, cx, y, ink, sw=PT_THIN, head=6))
        if branch:
            label, bnum, blines = branch
            b.append(refusal(y + h / 2 - step_h / 2, bnum, blines, label))
        prev_bottom = y + h
        y += h + gap

    return "".join(b), y


# --- FIG. 6  appointment sequence ------------------------------------------

def _fig6(t, ox, oy):
    ink = t["ink"]
    b = [p_fig(ox, oy, "FIG. 6", ink, sub="APPOINTMENT SEQUENCE")]
    lanes = [("CALLER", 800, ox + 30), ("APPARATUS", 200, ox + 160),
             ("UPSTREAM", 500, ox + 290)]
    top = oy + 34
    bottom = oy + 512
    for nm, num, x in lanes:
        b.append(p_rect(x - 52, top, 104, 30, ink, sw=PT_MED))
        b.append(p_txt(x, top + 20, nm, ink, size=9, anchor="middle", weight=700))
        b.append(line(x, top + 30, x, bottom, stroke=ink, sw=PT_THIN, dash="5 4"))
        b.append(p_ref(x, top - 10, (x, top), num, ink))

    cxs = [l[2] for l in lanes]

    def msg(yy, a, c, label, num, *, back=False, note=""):
        x1, x2 = cxs[a], cxs[c]
        out = [p_arrow(x1, yy, x2, yy, ink, sw=PT_MED, head=7)]
        mx = (x1 + x2) / 2
        out.append(p_txt(mx, yy - 7, label, ink, size=8.5, anchor="middle"))
        if note:
            out.append(p_txt(mx, yy + 12, note, ink, size=7.5, anchor="middle"))
        nx = max(x1, x2) + 42
        out.append(p_ref(nx, yy + 4, (max(x1, x2), yy), num, ink))
        return "".join(out)

    y = top + 60
    b.append(msg(y, 0, 1, "REQUEST", 802))
    y += 46
    b.append(msg(y, 1, 0, "REFUSAL + SIGNED", 804, note="APPOINTMENT FOR t1"))
    y += 58
    b.append(msg(y, 0, 1, "PREMATURE RETRY", 806))
    y += 46
    b.append(msg(y, 1, 0, "REFUSED, CREDITS", 808, note="CHARGED; SLOT UNCHANGED"))
    y += 62

    # the waiting interval
    b.append(line(cxs[0] - 16, y - 22, cxs[0] + 16, y - 22, stroke=ink, sw=PT_THIN))
    b.append(line(cxs[0] - 16, y + 18, cxs[0] + 16, y + 18, stroke=ink, sw=PT_THIN))
    # close the interval into a dimension line, or it reads as two loose ticks
    b.append(line(cxs[0] + 16, y - 22, cxs[0] + 16, y + 18, stroke=ink, sw=PT_THIN))
    b.append(p_txt(cxs[0] - 22, y + 2, "t1", ink, size=9, anchor="end",
                   family=MONO, weight=700))
    b.append(p_ref(cxs[0] + 54, y + 4, (cxs[0] + 16, y), 810, ink))

    y += 40
    b.append(msg(y, 0, 1, "PUNCTUAL ARRIVAL", 812))
    y += 46
    b.append(msg(y, 1, 2, "ADMITTED, FORWARDED", 814))
    y += 46
    b.append(msg(y, 2, 1, "RESULT", 816))
    y += 46
    b.append(msg(y, 1, 0, "RESPONSE + RECEIPT", 818))

    b.append(p_txt(ox, bottom + 26,
                   "PREMATURE RETRY (806) CANNOT ADVANCE t1.",
                   ink, size=8.5, family=MONO, spacing="0.3"))
    b.append(p_txt(ox, bottom + 40,
                   "WAITING IS THEREFORE BOTH CHEAPER AND FASTER.",
                   ink, size=8.5, family=MONO, spacing="0.3"))
    return "".join(b), bottom + 56


# --- FIG. 7  budget conservation across fan-out ----------------------------

def _fig7(t, ox, oy):
    """The mechanism the other figures do not show: a swarm cannot outspend
    its root, because fan-out divides a budget instead of multiplying one."""
    ink = t["ink"]
    b = [p_fig(ox, oy, "FIG. 7", ink, sub="BUDGET CONSERVATION")]
    cx = ox + 172
    r = 11

    def node(x, y, *, funded=True):
        return (f'<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="{ink}" '
                f'stroke-width="{PT_MED if funded else PT_THIN}"'
                + ('' if funded else ' stroke-dasharray="3 3"') + '/>')

    y0, y1, y2, y3 = oy + 52, oy + 122, oy + 192, oy + 268
    b.append(node(cx, y0))
    b.append(p_ref(cx - 54, y0 + 4, (cx - r, y0), 900, ink))
    b.append(p_txt(cx + 26, y0 + 4, "ROOT GRANT", ink, size=8, family=MONO))

    l1 = [cx - 96, cx, cx + 96]
    for x in l1:
        b.append(node(x, y1))
        b.append(line(cx, y0 + r, x, y1 - r, stroke=ink, sw=PT_THIN))

    l2 = []
    for i, px in enumerate(l1):
        for j in (-32, 0, 32):
            x = px + j
            l2.append(x)
            b.append(node(x, y2))
            b.append(line(px, y1 + r, x, y2 - r, stroke=ink, sw=PT_THIN))
    b.append(p_ref(ox + 6, y2 + 4, (min(l2) - r, y2), 902, ink))

    # the level at which the grant is exhausted
    ey = (y2 + y3) / 2
    b.append(line(ox + 4, ey, ox + 340, ey, stroke=ink, sw=PT_MED, dash="8 5"))
    # below the line, not above it: above, the descendants' drop lines run
    # straight through the lettering
    b.append(p_txt(ox + 4, ey + 15, "ROOT GRANT EXHAUSTED", ink, size=8.5,
                   weight=700, family=MONO, spacing="0.4"))
    b.append(p_ref(ox + 322, ey + 20, (ox + 322, ey), 906, ink))

    l3 = [ox + 22 + i * 26 for i in range(13)]
    for x in l3:
        b.append(node(x, y3, funded=False))
    for px in l2:
        b.append(line(px, y2 + r, px, ey, stroke=ink, sw=PT_THIN))
    b.append(p_ref(ox + 6, y3 + 42, (l3[0] - r + 4, y3 + r), 904, ink))

    b.append(p_txt(ox, y3 + 74,
                   "REFUSED AT THE EDGE (904). NO UPSTREAM CALL IS MADE,",
                   ink, size=8.5, family=MONO, spacing="0.3"))
    b.append(p_txt(ox, y3 + 88,
                   "AND A REFUSED NODE ISSUES NO CHILDREN OF ITS OWN.",
                   ink, size=8.5, family=MONO, spacing="0.3"))
    return "".join(b), y3 + 104


def build_patent3(t: dict) -> str:
    W = 980
    f5, y5 = _fig5(t, 78)
    f6, y6 = _fig6(t, 606, 78)
    f7, y7 = _fig7(t, 606, y6 + 34)
    h = max(y5, y7) + 26
    body = f5 + f6 + f7 + line(578, 70, 578, h - 40, stroke=t["ink"], sw=PT_THIN,
                               dash="3 5")
    return p_sheet(t, 3, 3, W, h, body,
                   "Patent-style figures, sheet 3: method flowchart and appointment sequence")


BUILDERS = {
    "logo": build_logo,
    "banner": build_banner,
    "pipeline": build_pipeline,
    "appointments": build_appointments,
    "header": build_header,
    "topology": build_topology,
    "internals": build_internals,
    "patent1": build_patent1,
    "patent2": build_patent2,
    "patent3": build_patent3,
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
