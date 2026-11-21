"""Draw the link preview for Mastodon, Slack and the like: site/og.png, 1200 × 630.

A region's cycling infrastructure in OSM at the newest snapshot, in radinfra.de's
colours and drawing order, inside its boundary, next to the wordmark. Drawn as SVG
and rasterised with rsvg-convert (librsvg); the text is set in Inter, as in the
logo. Run it after a build, or after scripts/fetch_data.sh:

    uv run python scripts/og_image.py [region]     # default germany
"""

import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import numpy as np
import shapely

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.builds import Region  # noqa: E402
from pipeline.style import legend_groups  # noqa: E402

WORDMARK = ROOT / "site" / "logo-wordmark-light.svg"
OUT = ROOT / "site" / "og.png"

W, H = 1200, 630
MAP_BOX = (585, 40, 1176, 590)   # left, top, right, bottom of the map
BACKGROUND = "#eef0f3"
CITY = "#ffffff"
CITY_EDGE = "#cfd4db"
INK = "#1f2937"
MUTED = "#6b7280"
LINE_WIDTH = 1.7


def projector(bounds):
    """lon/lat → pixels in MAP_BOX: equirectangular at the region's middle latitude, centred."""
    west, south, east, north = bounds
    lat0 = np.radians((south + north) / 2)
    kx = np.cos(lat0)
    left, top, right, bottom = MAP_BOX
    scale = min((right - left) / ((east - west) * kx), (bottom - top) / (north - south))
    ox = left + ((right - left) - (east - west) * kx * scale) / 2
    oy = top + ((bottom - top) - (north - south) * scale) / 2

    def project(coords):
        return np.column_stack((ox + (coords[:, 0] - west) * kx * scale,
                                oy + (north - coords[:, 1]) * scale))
    # Metres per pixel: a degree of latitude is 111 km.
    project.metres_per_px = 111_320 / scale
    return project


# librsvg's XML parser stops at about 10 MB of input, and Germany's lines made
# 24 MB. So lines shorter than a quarter pixel are left out (at Germany's scale a
# pixel is 1.6 km; Berlin's, 80 m), points after the first are relative, and a
# category's lines come in several paths.
PATH_CHARS = 2_000_000
SHORTEST_PX = 0.25


def _num(v):
    """1 decimal, without a leading zero: 0.3 → .3, -0.3 → -.3."""
    text = f"{v:.1f}".replace("-0.", "-.")
    return text[1:] if text.startswith("0.") else ("0" if text in ("0.0", "-0.0", "-.0", ".0") else text)


def path_data(geoms):
    """SVG path data for many lines, in pieces of at most PATH_CHARS; 1 decimal:
    invisible at this size, a third of the bytes."""
    pieces, parts, size = [], [], 0
    for line in shapely.get_parts(geoms):
        xy = shapely.get_coordinates(line)
        if len(xy) < 2:
            continue
        steps = np.round(np.diff(xy, axis=0), 1)
        part = f"M{xy[0, 0]:.1f} {xy[0, 1]:.1f}l" + " ".join(f"{_num(dx)} {_num(dy)}" for dx, dy in steps)
        if size + len(part) > PATH_CHARS and parts:
            pieces.append("".join(parts))
            parts, size = [], 0
        parts.append(part)
        size += len(part)
    if parts:
        pieces.append("".join(parts))
    return pieces


def main():
    region = Region(ROOT / "site" / "data" / (sys.argv[1] if len(sys.argv) > 1 else "germany"))
    build = region.build_dir(region.check())
    timeline = json.loads((build / "timeline.json").read_text(encoding="utf-8"))
    newest = timeline["snapshots"][-1]["key"]
    outline = json.loads((build / "outline.geojson").read_text(encoding="utf-8"))
    city = shapely.geometry.shape(outline["geometry"])
    project = projector(shapely.bounds(city))

    shortest_m = SHORTEST_PX * project.metres_per_px
    category, wkb = duckdb.connect().execute(
        f"SELECT category, geometry::BLOB FROM read_parquet('{build / "versions.parquet"}') "
        f"WHERE f <= {newest} AND t > {newest} AND length_m >= {shortest_m}"
    ).fetchnumpy().values()
    lines = shapely.from_wkb(np.asarray([bytes(b) for b in wkb], dtype=object))
    # Lines inside stay whole; only those across the boundary are cut, which for
    # Germany's outline is the slow part (pipeline/areas.py has the same).
    shapely.prepare(city)
    inside = shapely.contains_properly(city, lines)
    lines[~inside] = shapely.intersection(lines[~inside], city)
    lines = shapely.transform(lines, project)
    # Half a pixel: what is simplified away cannot be seen.
    lines = shapely.simplify(lines, 0.5)
    city_px = shapely.simplify(shapely.transform(city, project), 0.5)

    layers = []
    for group in legend_groups():   # bottom to top, as on the map
        mask = np.isin(category, group["categories"])
        if mask.any():
            layers += [f'<path d="{d}" stroke="{group["color"]}"/>' for d in path_data(lines[mask])]

    wordmark = base64.b64encode(WORDMARK.read_bytes()).decode()
    mark_w = 450
    mark_h = mark_w * 256 / 1187.51
    city_d = "".join(
        "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in shapely.get_coordinates(ring)) + "Z"
        for polygon in shapely.get_parts(city_px)
        for ring in [shapely.get_exterior_ring(polygon), *shapely.get_interior_ring(polygon, range(shapely.get_num_interior_rings(polygon)))]
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="{W}" height="{H}" fill="{BACKGROUND}"/>
  <path d="{city_d}" fill="{CITY}" stroke="{CITY_EDGE}" stroke-width="2" fill-rule="evenodd"/>
  <g fill="none" stroke-width="{LINE_WIDTH}" stroke-linecap="round" stroke-linejoin="round">
    {''.join(layers)}
  </g>
  <image x="56" y="226" width="{mark_w}" height="{mark_h:.1f}" xlink:href="data:image/svg+xml;base64,{wordmark}"/>
  <g font-family="Inter" fill="{INK}">
    <text x="66" y="386" font-size="36" font-weight="600">Radinfrastruktur in {timeline.get("region") or "Berlin"}</text>
    <text x="66" y="432" font-size="30" fill="{MUTED}">in OpenStreetMap, seit 2016</text>
    <text x="66" y="560" font-size="22" fill="{MUTED}">Klassifiziert wie bei radinfra.de</text>
  </g>
</svg>
"""
    with tempfile.TemporaryDirectory(prefix="og-") as tmp:
        svg_path = Path(tmp) / "og.svg"
        svg_path.write_text(svg, encoding="utf-8")
        subprocess.run(["rsvg-convert", "-w", str(W), "-h", str(H), "-o", str(OUT), str(svg_path)], check=True)
    print(f"{OUT}: {OUT.stat().st_size / 1e3:.0f} kB, {len(lines):,} lines of {timeline['snapshots'][-1]['date']}")


if __name__ == "__main__":
    main()
