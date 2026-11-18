"""Draw the radinfra-history logo: site/logo.svg, site/logo-wordmark.svg, site/favicon.svg,
and site/logo-wordmark-light.svg, which has no dark variant, for the page's panel.

The mark is a bicycle wheel that is also a clock ("Rad" in German is both wheel
and bike). Its rim runs counter-clockwise like a history/rewind arrow and is drawn
as a way in OSM terms: segments between vertices, coloured in radinfra.de's
category colours, from dashed markings to separated cycleways and protected lanes,
so along the arrow the infrastructure gets better over time.

The wordmark is outlined from Inter (SIL Open Font License), so the files need
no fonts to render.

    python3 scripts/logo.py        # needs fontTools and Inter installed
"""

import math
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

SITE = Path(__file__).resolve().parent.parent / "site"
FONTS = Path("/usr/share/fonts/opentype/inter")

# radinfra.de details style colours (site/radinfra_details_style.json).
PINK = "#f08ed5"     # Gehweg, Rad frei
TEAL = "#2dd4bf"     # Schutz- und Radfahrstreifen
PURPLE = "#7a0ff5"   # geschützter Radfahrstreifen
BLUE = "#174ed9"     # getrennter Radweg
INK = "#1f2937"
SPOKE = "#cbd5e1"

C = 128.0            # centre of the 256 × 256 mark
R = 92.0             # rim centre line
W = 20.0             # rim width


def pt(theta, r=R):
    """Point at math angle theta (degrees, counter-clockwise from 3 o'clock)."""
    a = math.radians(theta)
    return C + r * math.cos(a), C - r * math.sin(a)


def f(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def arc(theta0, theta1, r=R):
    """SVG path for an arc from theta0 to theta1, counter-clockwise on screen."""
    x0, y0 = pt(theta0, r)
    x1, y1 = pt(theta1, r)
    large = 1 if (theta1 - theta0) % 360 > 180 else 0
    # Increasing theta is counter-clockwise on screen, which is sweep-flag 0.
    return f"M{f(x0)} {f(y0)}A{f(r)} {f(r)} 0 {large} 0 {f(x1)} {f(y1)}"


def arrowhead(theta_base, length_deg, half_width):
    """Triangle whose base sits across the rim at theta_base, pointing along it."""
    bx_out, by_out = pt(theta_base, R + half_width)
    bx_in, by_in = pt(theta_base, R - half_width)
    tx, ty = pt(theta_base + length_deg, R)
    return f"M{f(bx_out)} {f(by_out)}L{f(tx)} {f(ty)}L{f(bx_in)} {f(by_in)}Z"


# The rim: from the tail at ~8 o'clock round to ~10 o'clock, counter-clockwise.
TAIL = 205.0
HEAD = 507.0                      # = 147°, where the arrowhead's base sits
# Along the arrow the infrastructure gets better: shared footway, advisory lane,
# cycle lane, separated cycleway, and at the head the protected cycle lane.
SEGMENTS = [                      # (colour, dashed) from oldest to newest
    (PINK, True),
    (TEAL, True),
    (TEAL, False),
    (BLUE, False),
    (PURPLE, False),
]
HEAD_COLOUR = PURPLE


def favicon():
    """The mark redrawn for 16–48 px: a bolder rim in four colours with the dashed
    start, the vertices between the colours, and the clock hands. No spokes, which
    turn to mush at that size."""
    r, w = 84.0, 36.0
    tail, head = 208.0, 500.0
    colours = [PINK, TEAL, BLUE, PURPLE]
    span = (head - tail) / len(colours)
    parts = []
    for i, colour in enumerate(colours):
        a0 = tail + i * span
        # A hair of overlap where two colours meet, against anti-aliasing seams.
        a1 = a0 + span + (0.6 if i < len(colours) - 1 else 0)
        dash = ""
        if i == 0:
            # Two dashes: at 16 px more would blur into a solid line.
            length = math.radians(a1 - a0) * r
            on = length / (2 * 1.7)
            off = (length - 2 * on) / 2
            dash = f' stroke-dasharray="{f(on)} {f(off)}" stroke-dashoffset="{f(-off / 2)}"'
        parts.append(f'<path d="{arc(a0, a1, r)}" fill="none" stroke="{colour}" stroke-width="{f(w)}"{dash}/>')

    bx_out, by_out = pt(head - 0.5, r + w * 1.08)
    bx_in, by_in = pt(head - 0.5, r - w * 1.08)
    tx, ty = pt(head + 34, r)
    parts.append(f'<path d="M{f(bx_out)} {f(by_out)}L{f(tx)} {f(ty)}L{f(bx_in)} {f(by_in)}Z" '
                 f'fill="{HEAD_COLOUR}" stroke="{HEAD_COLOUR}" stroke-width="4" stroke-linejoin="round"/>')

    hx, hy = pt(0, 34)
    mx, my = pt(90, 50)
    parts.append(f'<path class="ink" d="M{f(C)} {f(C)}L{f(hx)} {f(hy)}" stroke="{INK}" '
                 f'stroke-width="20" stroke-linecap="round" fill="none"/>')
    parts.append(f'<path class="ink" d="M{f(C)} {f(C)}L{f(mx)} {f(my)}" stroke="{INK}" '
                 f'stroke-width="16" stroke-linecap="round" fill="none"/>')
    parts.append(f'<circle class="ink-fill" cx="{f(C)}" cy="{f(C)}" r="14" fill="{INK}"/>')
    parts.append(f'<circle class="paper" cx="{f(C)}" cy="{f(C)}" r="5.5" fill="#ffffff"/>')

    # Vertices only where the colours meet; one at the tail as well is too busy.
    for i in range(1, len(colours)):
        x, y = pt(tail + i * span, r)
        parts.append(f'<circle class="paper ink-stroke" cx="{f(x)}" cy="{f(y)}" r="11" '
                     f'fill="#ffffff" stroke="{INK}" stroke-width="4.5"/>')
    return parts


def mark(detail=True):
    """The mark's SVG elements. detail=False drops the dashes for small sizes."""
    parts = []
    if detail:
        # Spokes: a wheel, and at twelve positions also the hours of a clock.
        spokes = []
        for i in range(12):
            theta = 90 - i * 30
            x0, y0 = pt(theta, 17)
            x1, y1 = pt(theta, R - W / 2 - 7)
            spokes.append(f"M{f(x0)} {f(y0)}L{f(x1)} {f(y1)}")
        parts.append(f'<path class="spoke" d="{"".join(spokes)}" fill="none" stroke="{SPOKE}" '
                     f'stroke-width="3" stroke-linecap="round"/>')

    span = (HEAD - TAIL) / len(SEGMENTS)
    for i, (colour, dashed) in enumerate(SEGMENTS):
        a0 = TAIL + i * span
        a1 = a0 + span
        # Dashes as in the map's dashed categories, sized to fit the arc evenly.
        dash = ""
        if dashed and detail:
            length = math.radians(span) * R
            n = 4
            on = length / (n * 1.6)
            off = (length - n * on) / (n - 0.0)
            dash = f' stroke-dasharray="{f(on)} {f(off)}" stroke-dashoffset="{f(-off / 2)}"'
        parts.append(f'<path d="{arc(a0, a1)}" fill="none" stroke="{colour}" stroke-width="{f(W)}"{dash}/>')

    parts.append(f'<path d="{arrowhead(HEAD - 0.5, 27, W * 1.25)}" fill="{HEAD_COLOUR}" '
                 f'stroke="{HEAD_COLOUR}" stroke-width="3" stroke-linejoin="round"/>')

    # Clock hands at three o'clock, the hub as the wheel's axle. (Ten past ten
    # reads as a tick mark at favicon size.)
    hx, hy = pt(0, 42)
    mx, my = pt(90, 60)
    parts.append(f'<path class="ink" d="M{f(C)} {f(C)}L{f(hx)} {f(hy)}" stroke="{INK}" '
                 f'stroke-width="11" stroke-linecap="round"/>')
    parts.append(f'<path class="ink" d="M{f(C)} {f(C)}L{f(mx)} {f(my)}" stroke="{INK}" '
                 f'stroke-width="8" stroke-linecap="round"/>')
    parts.append(f'<circle class="ink-fill" cx="{f(C)}" cy="{f(C)}" r="12" fill="{INK}"/>')
    parts.append(f'<circle class="paper" cx="{f(C)}" cy="{f(C)}" r="4.5" fill="#ffffff"/>')

    # Way vertices between the segments and at the tail, as an OSM editor draws them.
    node_r = 8.5 if detail else 10
    for i in range(len(SEGMENTS)):
        x, y = pt(TAIL + i * span)
        parts.append(f'<circle class="paper ink-stroke" cx="{f(x)}" cy="{f(y)}" r="{f(node_r)}" '
                     f'fill="#ffffff" stroke="{INK}" stroke-width="3.5"/>')
    return parts


DARK_CSS = """<style>
@media (prefers-color-scheme: dark) {
  .ink { stroke: #f3f4f6 }
  .ink-fill { fill: #f3f4f6 }
  .ink-stroke { stroke: #f3f4f6 }
  .paper { fill: #111827 }
  .spoke { stroke: #4b5563 }
  .word { fill: #f3f4f6 }
  .accent { fill: #7aa2ff }
}
</style>"""


def text_path(text, font_file, size, x, baseline, tracking=0.0):
    """Outline `text` as one SVG path. Returns (path d, advance width)."""
    font = TTFont(font_file)
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = size / font["head"].unitsPerEm
    pen = SVGPathPen(glyphs)
    cursor = 0.0
    for ch in text:
        name = cmap[ord(ch)]
        glyph = glyphs[name]
        glyph.draw(TransformPen(pen, (scale, 0, 0, -scale, x + cursor, baseline)))
        cursor += glyph.width * scale + tracking
    return pen.getCommands(), cursor - tracking


def svg(width, height, body, title, viewbox=None, dark=True):
    """dark=False for the page's own panel, which stays light in dark mode: there
    the dark variant would put light ink on a white panel."""
    viewbox = viewbox or f"0 0 {f(width)} {f(height)}"
    style = f"{DARK_CSS}\n" if dark else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" '
            f'width="{f(width)}" height="{f(height)}" role="img" aria-label="{title}">\n'
            f'<title>{title}</title>\n{style}{body}\n</svg>\n')


def main():
    title = "radinfra-history"
    (SITE / "logo.svg").write_text(svg(256, 256, "\n".join(mark()), title), encoding="utf-8")
    # Cropped close to the rim, so the mark fills the tab icon.
    (SITE / "favicon.svg").write_text(
        svg(256, 256, "\n".join(favicon()), title, viewbox="6 6 244 244"), encoding="utf-8")

    # Lockup: mark, then "radinfra" bold and "history" in the accent colour.
    height = 256
    size = 132
    baseline = 128 + 0.52 * 1056 / 2048 * size   # x-height centred on the wheel
    gap = 34
    space = 30
    x = 256 + gap
    d1, w1 = text_path("radinfra", FONTS / "InterDisplay-Bold.otf", size, x, baseline, tracking=-2.5)
    d2, w2 = text_path("history", FONTS / "InterDisplay-Medium.otf", size, x + w1 + space, baseline, tracking=-2.0)
    width = x + w1 + space + w2 + 16
    body = "\n".join(mark()) + (
        f'\n<path class="word" d="{d1}" fill="{INK}"/>'
        f'\n<path class="accent" d="{d2}" fill="{BLUE}"/>'
    )
    (SITE / "logo-wordmark.svg").write_text(svg(width, height, body, title), encoding="utf-8")
    (SITE / "logo-wordmark-light.svg").write_text(svg(width, height, body, title, dark=False), encoding="utf-8")
    for name in ("logo.svg", "favicon.svg", "logo-wordmark.svg", "logo-wordmark-light.svg"):
        print(f"{SITE / name}: {(SITE / name).stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
