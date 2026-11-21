"""Tile the versions into PMTiles archives of MapLibre Tiles with manby
(tippecanoe-compatible). Three layers, each in an archive of its own:

* `bikelanes` (z12–14) in <slug>.pmtiles: every version as its own feature, with
  id, name and the feature id the hover and the popup need.
* `bikelanes_lo` (z9–11) in <slug>_lo.pmtiles: only what drawing needs
  (category, side, f, t), and versions with the same values merged into one
  feature per tile. An overview tile then holds a few hundred features instead of
  tens of thousands, which is what moving the slider costs: every date change
  re-filters every loaded tile.
* `bikelanes_far` (z5–8) in <slug>_far.pmtiles, for Germany seen whole: like the
  overview, without the side of the road (a pixel apart at these zooms), and
  without short versions, as radinfra.de leaves lines under 100 m out below z9
  (tilda-geo, bikelane_generalization.lua). Here the length grows as the zoom
  shrinks (FAR_LENGTHS): at z5 a pixel is about 2 km, and Germany's 3.2 million
  versions would make tiles of 3 MB. A version is in or out by its own length,
  whatever the date, so scrubbing the timeline does not make lines flicker.

Apart from those short lines far out, no feature is dropped at any zoom. tippecanoe's usual size control
(--drop-densest-as-needed) would thin out versions unevenly, so a street could
vanish at one date and come back at the next while scrubbing.
"""

import json
import os
from pathlib import Path

import duckdb

from . import tools

ATTRIBUTION = (
    '<a href="https://www.openstreetmap.org/copyright" target="_blank">© OpenStreetMap-Mitwirkende</a> · '
    'Klassifikation nach <a href="https://radinfra.de" target="_blank">radinfra.de</a> (tilda-geo), '
    'berechnet mit <a href="https://github.com/rush42/OSMnexus" target="_blank">OSMnexus</a>'
)

DETAIL_LAYER = "bikelanes"
OVERVIEW_LAYER = "bikelanes_lo"
FAR_LAYER = "bikelanes_far"

# Attributes the map reads at detail zooms; everything else stays out of the tiles.
# The popup's dates (cf, ct, pc, nc) are not among them: they come from a file of
# their own on click (versions.write_spans), which keeps these tiles 16 % smaller.
DETAIL_ATTRIBUTES = ["id", "category", "side", "name", "f", "t"]
# At overview zooms only what the filters and the style need.
OVERVIEW_ATTRIBUTES = ["category", "side", "f", "t"]
FAR_ATTRIBUTES = ["category", "f", "t"]

# Below the overview: from which zoom a version of at least this many metres is
# drawn. 18 MB for Germany, the largest tile 1.6 MB at z5 (3.1 MB with every line).
FAR_MINZOOM = 5
FAR_LENGTHS = ((5, 250), (7, 150), (8, 100))

# First zoom with the detail layer. The page switches its layers at the same zoom.
DETAIL_MINZOOM = 12

# MapLibre Tiles rather than Mapbox Vector Tiles: 23 % smaller here, 39 % at the
# overview zooms, read by MapLibre GL JS 5.12 and later (encoding: "mlt").
TILE_FORMAT = "mlt"


def overview_path(out_pmtiles, suffix="_lo"):
    """manby cannot join MLT archives, so the overview zooms get a file of their own."""
    out_pmtiles = Path(out_pmtiles)
    return out_pmtiles.with_name(f"{out_pmtiles.stem}{suffix}{out_pmtiles.suffix}")


def _far_input(versions_parquet, path):
    """The versions long enough for the far zooms, each with the zoom it starts at (mz)."""
    cases = " ".join(f"WHEN length_m >= {metres} THEN {zoom}" for zoom, metres in FAR_LENGTHS)
    shortest = min(metres for _, metres in FAR_LENGTHS)
    duckdb.connect().execute(f"""
        COPY (
            SELECT {", ".join(FAR_ATTRIBUTES)}, CASE {cases} END AS mz, geometry
            FROM read_parquet('{versions_parquet}') WHERE length_m >= {shortest}
        ) TO '{path}' (FORMAT parquet, COMPRESSION zstd)
    """)
    # manby's filter: a feature shows from its mz on.
    return {"*": ["any", *(["all", ["==", "mz", zoom], [">=", "$zoom", zoom]] for zoom, _ in FAR_LENGTHS)]}


def run_manby(out, layer, minzoom, maxzoom, attributes, extra, source):
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
    """Detail zooms into out_pmtiles, overview and far zooms next to it (overview_path)."""
    out_pmtiles = Path(out_pmtiles)
    out_pmtiles.parent.mkdir(parents=True, exist_ok=True)
    overview = overview_path(out_pmtiles)
    far = overview_path(out_pmtiles, "_far")
    # Put features with equal values next to each other, then merge them.
    merge = ["--reorder", "--coalesce"]
    far_input = far.with_name(".far.parquet")
    try:
        far_filter = _far_input(versions_parquet, far_input)
        run_manby(far, FAR_LAYER, FAR_MINZOOM, minzoom - 1, [*FAR_ATTRIBUTES, "mz"],
               [*merge, "-j", json.dumps(far_filter)], far_input)
    finally:
        far_input.unlink(missing_ok=True)
    run_manby(overview, OVERVIEW_LAYER, minzoom, DETAIL_MINZOOM - 1, OVERVIEW_ATTRIBUTES, merge, versions_parquet)
    run_manby(out_pmtiles, DETAIL_LAYER, DETAIL_MINZOOM, maxzoom, DETAIL_ATTRIBUTES,
           # The version id becomes the feature id, for hover and popup.
           ["--use-attribute-for-id=vid"], versions_parquet)
    for path in (far, overview, out_pmtiles):
        print(f"  {path}: {path.stat().st_size / 1e6:.1f} MB", flush=True)
    return out_pmtiles
