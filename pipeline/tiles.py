"""Tile the versions into one PMTiles archive with manby (tippecanoe-compatible).

Two layers, joined into one archive:

* `bikelanes` (z12–14): every version as its own feature, with id, name and the
  MVT feature id the hover and the popup need.
* `bikelanes_lo` (z9–11): only what drawing needs (category, side, f, t), and
  versions with the same values merged into one feature per tile. An overview
  tile then holds a few hundred features instead of tens of thousands, which is
  what moving the slider costs: every date change re-filters every loaded tile.

No feature is dropped at any zoom. tippecanoe's usual size control
(--drop-densest-as-needed) would thin out versions unevenly, so a street could
vanish at one date and come back at the next while scrubbing.
"""

import tempfile
from pathlib import Path

from . import tools

ATTRIBUTION = (
    '<a href="https://www.openstreetmap.org/copyright" target="_blank">© OpenStreetMap-Mitwirkende</a> · '
    'Klassifikation nach <a href="https://radinfra.de" target="_blank">radinfra.de</a> (tilda-geo), '
    'berechnet mit <a href="https://github.com/rush42/OSMnexus" target="_blank">OSMnexus</a>'
)

DETAIL_LAYER = "bikelanes"
OVERVIEW_LAYER = "bikelanes_lo"

# Attributes the map reads at detail zooms; everything else stays out of the tiles.
DETAIL_ATTRIBUTES = ["id", "category", "side", "name", "f", "t"]
# At overview zooms only what the filters and the style need.
OVERVIEW_ATTRIBUTES = ["category", "side", "f", "t"]

# First zoom with the detail layer. The page switches its layers at the same zoom.
DETAIL_MINZOOM = 12


def _manby(out, layer, minzoom, maxzoom, attributes, extra, source):
    cmd = [
        tools.manby(), "-o", out, "--force", "-l", layer,
        "-Z", str(minzoom), "-z", str(maxzoom),
        # Base zoom at the minimum: nothing is thinned out below it either.
        "-B", str(minzoom),
        "--no-feature-limit", "--no-tile-size-limit",
        *extra,
    ]
    for attribute in attributes:
        cmd += ["-y", attribute]
    cmd.append(source)
    tools.run(cmd)


def build_tiles(versions_parquet, out_pmtiles, minzoom=9, maxzoom=14):
    out_pmtiles = Path(out_pmtiles)
    out_pmtiles.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tiles-") as tmp:
        overview = Path(tmp) / "overview.pmtiles"
        detail = Path(tmp) / "detail.pmtiles"
        _manby(overview, OVERVIEW_LAYER, minzoom, DETAIL_MINZOOM - 1, OVERVIEW_ATTRIBUTES,
               # Put features with equal values next to each other, then merge them.
               ["--reorder", "--coalesce"], versions_parquet)
        _manby(detail, DETAIL_LAYER, DETAIL_MINZOOM, maxzoom, DETAIL_ATTRIBUTES,
               # The version id becomes the MVT feature id, for hover and popup.
               ["--use-attribute-for-id=vid"], versions_parquet)
        tools.run([
            tools.manby(), "join", "-o", out_pmtiles, "--force",
            "-n", "radinfra-history", "-A", ATTRIBUTION,
            overview, detail,
        ])
    size = out_pmtiles.stat().st_size
    print(f"  {out_pmtiles}: {size / 1e6:.1f} MB", flush=True)
    return out_pmtiles
