# radinfra-history

How the cycling infrastructure recorded in OpenStreetMap has changed over time,
classified the way [radinfra.de](https://radinfra.de) classifies it today, on one
map with a time slider.

Pilot: **Berlin, quarterly from January 2020 to the newest state of the history file.**

The map draws radinfra.de's own "details" style — same categories, colours and
dashes — against data computed here for every snapshot. Each side of a road is
its own line, offset to its side. Clicking a line shows since when it has been
in OSM in that form, with links to the way and its version history.

Not a publication of radinfra.de or FixMyCity.

## How it works

```
berlin-internal.osh.pbf                 full history, with personal metadata
  │  osmium time-filter, every 3 months, metadata stripped
  ▼
data/snapshots/<date>.osm.pbf           the state of OSM at each date
  │  OSMnexus, configs/tilda (bikelanes topic)
  ▼
data/classified/<date>.parquet          radinfra categories per way / side of a road
  │  pipeline/versions.py
  ▼
data/berlin_versions.parquet            every distinct version once, with a lifespan
  │  manby (tippecanoe-compatible)
  ▼
site/tiles/berlin.pmtiles               one archive for all dates
site/data/berlin_timeline.json          km per category and snapshot
```

**Classification.** [OSMnexus](https://github.com/rush42/OSMnexus) reimplements the
radinfra.de classification from [tilda-geo](https://github.com/FixMyBerlin/tilda-geo)
as data-driven rules and runs straight on a PBF, without PostGIS or osm2pgsql. Its
categories are spelt in snake_case and translated to tilda's ids in
[pipeline/style.py](pipeline/style.py); an unknown category fails the build.
OSMnexus classifies by tags only, while radinfra.de additionally estimates from
geometry whether a path runs alongside a road. That only moves objects between
`_adjoining`, `_isolated` and `_adjoiningOrIsolated`, which the details style draws
in one colour, so the map is unaffected.

**Versions instead of one tileset per date.** Consecutive snapshots are compared per
object (a way, or one side of a road: `way/123/cycleway/left`). A new version
starts whenever category, geometry or name change, and ends when the object
disappears. Each version is tiled once, with

- `f` — first snapshot it is part of (`YYYYMMDD`)
- `t` — first snapshot it is no longer part of, or `99991231`

The state at snapshot `D` is exactly `f <= D < t`. The page keeps `D` in MapLibre's
[global state](https://maplibre.org/maplibre-style-spec/expressions/#global-state)
and every layer filters on it, so moving the slider changes one value and loads
nothing. An unchanged cycleway is one feature however many snapshots it spans.
[tests/test_versions.py](tests/test_versions.py) checks that the filter reproduces
every snapshot exactly.

No feature is dropped at any zoom (`--no-feature-limit --no-tile-size-limit`).
tippecanoe's usual `--drop-densest-as-needed` would thin out versions unevenly, and
streets would flicker in and out while scrubbing.

**Lengths** are counted per side of the road: a street with cycle lanes on both
sides counts twice. Categories the details style does not draw (`cyclewayLink`)
are left out, as on radinfra.de's map.

## What the map shows and what it does not

- **What OSM knew, not what was built.** Mapping campaigns show up as jumps.
- **Today's rules on old data.** Every snapshot is classified with the current
  rules, which keeps the method constant, but older tagging lands more often in
  "Führungsform unklar".
- When a cycleway moves from tags on the road to a way of its own, its line moves
  a few metres. That is a change in mapping, not new infrastructure.
- Snapshot granularity: a change is dated to the snapshot it first appears in; the
  popup says between which two snapshots it happened.

## Data and privacy

Full-history files from Geofabrik's internal server carry user names and ids and
are only available with an OSM login. They stay out of the repository
(`.gitignore`). Snapshots are cut with `add_metadata=false`, so nothing downstream
of the first step — classifications, versions, tiles — contains any user data.

Map data © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright).

## Running it

Needs `osmium-tool`, [uv](https://docs.astral.sh/uv/) and Linux x86-64 (for the
OSMnexus release binary).

```bash
uv sync
./scripts/fetch_osmnexus.sh            # pinned OSMnexus binary + tilda config → .tools/

uv run python -m pipeline all --osh berlin-internal.osh.pbf --start 2020-01-01 --step 3
uv run python scripts/serve.py         # http://127.0.0.1:8765
```

Each step also runs on its own (`snapshots`, `classify`, `versions`, `tiles`);
classifications are reused unless `--force`. Instead of a history file, dated
extracts work too, such as Geofabrik's yearly public `berlin-YYMMDD.osm.pbf`:

```bash
uv run python -m pipeline all --pbf 'data/dated/berlin-*.osm.pbf'
```

`scripts/serve.py` instead of `python -m http.server`, because PMTiles reads byte
ranges and the stock server ignores `Range`.

Berlin, 29 snapshots on a 2-core machine: about 15 s per snapshot to cut and 10 s to
classify.

## Publishing

The built tiles and timeline are not committed to `main`. They live on the `data`
branch as a single commit that is replaced on every publish, so old tilesets do
not pile up in the history:

```bash
./scripts/publish_data.sh       # force-pushes `data`, then starts the Pages workflow
```

[.github/workflows/pages.yml](.github/workflows/pages.yml) copies them next to the
page and deploys `site/` to GitHub Pages, on one origin with the page. For Germany
as a whole the archive will outgrow GitHub; then the tiles move to object storage
(B2) with a CORS rule that allows `Range`, and only `PMTILES_URL` in
[site/config.js](site/config.js) changes. `scripts/fetch_data.sh` puts the
published data into `site/` for local work on the page.

## Credits

- Classification: [radinfra.de](https://radinfra.de) / [tilda-geo](https://github.com/FixMyBerlin/tilda-geo)
  by FixMyCity; style file `site/radinfra_details_style.json` taken from their tiles.
- [OSMnexus](https://github.com/rush42/OSMnexus) (AGPL-3.0), used as a binary with its `configs/tilda`.
- [manby](https://github.com/birkskyum/manby) for tiling, [osmium](https://osmcode.org/osmium-tool/) for snapshots.
- Page layout and interaction follow [vizsim/bikeneat](https://github.com/vizsim/bikeneat).
