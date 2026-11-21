"""Tile the versions into PMTiles archives of MapLibre Tiles with manby
(tippecanoe-compatible). Two layers, each in an archive of its own:

* `bikelanes` (z12–14) in <slug>.pmtiles: every version as its own feature, with
  id, name and the feature id the hover and the popup need.
* `bikelanes_lo` (z9–11) in <slug>_lo.pmtiles: only what drawing needs
  (category, side, f, t), and versions with the same values merged into one
  feature per tile. An overview tile then holds a few hundred features instead of
  tens of thousands, which is what moving the slider costs: every date change
  re-filters every loaded tile.

No feature is dropped at any zoom. tippecanoe's usual size control
(--drop-densest-as-needed) would thin out versions unevenly, so a street could
vanish at one date and come back at the next while scrubbing.
"""

import os
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
# The popup's dates (cf, ct, pc, nc) are not among them: they come from a file of
# their own on click (versions.write_spans), which keeps these tiles 16 % smaller.
DETAIL_ATTRIBUTES = ["id", "category", "side", "name", "f", "t"]
# At overview zooms only what the filters and the style need.
OVERVIEW_ATTRIBUTES = ["category", "side", "f", "t"]

# First zoom with the detail layer. The page switches its layers at the same zoom.
DETAIL_MINZOOM = 12

# MapLibre Tiles rather than Mapbox Vector Tiles: 23 % smaller here, 39 % at the
# overview zooms, read by MapLibre GL JS 5.12 and later (encoding: "mlt").
TILE_FORMAT = "mlt"


def overview_path(out_pmtiles):
    """manby cannot join MLT archives, so the overview zooms get a file of their own."""
    out_pmtiles = Path(out_pmtiles)
    return out_pmtiles.with_name(f"{out_pmtiles.stem}_lo{out_pmtiles.suffix}")


def _manby(out, layer, minzoom, maxzoom, attributes, extra, source):
    """Run in the output's folder, as plain `manby` with relative names: manby
    writes its command line into the archive (generator_options), and so the
    archive does not depend on where it was built or name a local path."""
    out = Path(out)
    here = out.parent
    cmd = [
        "manby", "-o", out.name, "--force", "-l", layer,
        "-Z", str(minzoom), "-z", str(maxzoom),
        # Base zoom at the minimum: nothing is thinned out below it either.
        "-B", str(minzoom),
        "--no-feature-limit", "--no-tile-size-limit",
        f"--tile-format={TILE_FORMAT}",
        "--name=radinfra-history", f"--attribution={ATTRIBUTION}",
        *extra,
    ]
    for attribute in attributes:
        cmd += ["-y", attribute]
    cmd.append(os.path.relpath(source, here))
    tools.run(cmd, cwd=here, executable=tools.manby())


def build_tiles(versions_parquet, out_pmtiles, minzoom=9, maxzoom=14):
    """Detail zooms into out_pmtiles, overview zooms next to it (overview_path)."""
    out_pmtiles = Path(out_pmtiles)
    out_pmtiles.parent.mkdir(parents=True, exist_ok=True)
    overview = overview_path(out_pmtiles)
    _manby(overview, OVERVIEW_LAYER, minzoom, DETAIL_MINZOOM - 1, OVERVIEW_ATTRIBUTES,
           # Put features with equal values next to each other, then merge them.
           ["--reorder", "--coalesce"], versions_parquet)
    _manby(out_pmtiles, DETAIL_LAYER, DETAIL_MINZOOM, maxzoom, DETAIL_ATTRIBUTES,
           # The version id becomes the feature id, for hover and popup.
           ["--use-attribute-for-id=vid"], versions_parquet)
    for path in (overview, out_pmtiles):
        print(f"  {path}: {path.stat().st_size / 1e6:.1f} MB", flush=True)
    return out_pmtiles
