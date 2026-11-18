"""Tile the versions into one PMTiles archive with manby (tippecanoe-compatible).

No feature is dropped at any zoom. tippecanoe's usual size control
(--drop-densest-as-needed) would thin out versions unevenly, so a street could
vanish at one date and come back at the next while scrubbing. Detail is limited
by the minimum zoom instead.
"""

from pathlib import Path

from . import tools

ATTRIBUTION = (
    '<a href="https://www.openstreetmap.org/copyright" target="_blank">© OpenStreetMap-Mitwirkende</a> · '
    'Klassifikation nach <a href="https://radinfra.de" target="_blank">radinfra.de</a> (tilda-geo), '
    'berechnet mit <a href="https://github.com/rush42/OSMnexus" target="_blank">OSMnexus</a>'
)

# Attributes the map reads. Everything else in the versions file stays out of the tiles.
TILE_ATTRIBUTES = ["id", "category", "side", "name", "f", "t"]


def build_tiles(versions_parquet, out_pmtiles, minzoom=9, maxzoom=14, layer="bikelanes"):
    out_pmtiles = Path(out_pmtiles)
    out_pmtiles.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        tools.manby(),
        "-o", out_pmtiles, "--force",
        "-l", layer,
        "-n", "radinfra-history",
        "-A", ATTRIBUTION,
        "-Z", str(minzoom), "-z", str(maxzoom),
        # Base zoom at the minimum: nothing is thinned out below it either.
        "-B", str(minzoom),
        "--no-feature-limit", "--no-tile-size-limit",
        # The version id becomes the MVT feature id, which the hover halo filters on.
        "--use-attribute-for-id=vid",
    ]
    for attribute in TILE_ATTRIBUTES:
        cmd += ["-y", attribute]
    cmd.append(versions_parquet)
    tools.run(cmd)
    size = out_pmtiles.stat().st_size
    print(f"  {out_pmtiles}: {size / 1e6:.1f} MB", flush=True)
    return out_pmtiles
