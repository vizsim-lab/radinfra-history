<p align="center"><img src="site/logo-wordmark.svg" alt="radinfra-history" height="80"></p>

# radinfra-history

How the cycling infrastructure recorded in OpenStreetMap has changed over time,
classified the way [radinfra.de](https://radinfra.de) classifies it today, on one
map with a time slider.

Pilot: **Berlin, quarterly from January 2016 to the end of the history file, and from
then on every quarter from Geofabrik's public extract** (see [Updates](#updates)).

The map draws radinfra.de's own "details" style — same categories, colours and
dashes — against data computed here for every snapshot. Each side of a road is
its own line, offset to its side. Clicking a line shows since when it has been
in OSM in that form, with links to the way and its version history. For Berlin
the background can show the aerial photo that matches the selected date.

Not a publication of radinfra.de or FixMyCity.

## How it works

```
berlin-internal.osh.pbf                 full history, with personal metadata
  │  osmium time-filter, every 3 months, metadata stripped
  ▼
data/berlin/snapshots/<date>.osm.pbf    the state of OSM at each date
  │  OSMnexus, configs/tilda (bikelanes topic)
  ▼
data/berlin/classified/<date>.parquet   radinfra categories per way / side of a road
  │  pipeline/versions.py
  ▼                                     one build: site/data/berlin/builds/<id>/
versions.parquet                        every distinct version once, with a lifespan
  │  pipeline/areas.py (OSM boundaries)     │  manby (tippecanoe-compatible), MLT
  ▼                                         ▼
areas.json                              tiles_lo.pmtiles, tiles.pmtiles
  km per category, snapshot and area      z9–11 and z12–14, for all dates
areas.geojson                           spans.bin
  Bezirke and Ortsteile                   the popup's dates, 6 bytes per version
                                        timeline.json
                                          km per category and snapshot (whole extract)
```

Every command from `versions` on makes a new build
([pipeline/builds.py](pipeline/builds.py)); `site/data/berlin/timeline.json`
points at the current one.

**Classification.** [OSMnexus](https://github.com/rush42/OSMnexus) reimplements the
radinfra.de classification from [tilda-geo](https://github.com/FixMyBerlin/tilda-geo)
as data-driven rules and runs straight on a PBF, without PostGIS or osm2pgsql. Its
categories are spelt in snake_case and translated to tilda's ids in
[pipeline/style.py](pipeline/style.py); an unknown category fails the build.
OSMnexus classifies by tags only, while radinfra.de additionally estimates from
geometry whether a path runs alongside a road. That only moves objects between
`_adjoining`, `_isolated` and `_adjoiningOrIsolated`, which the details style draws
in one colour, so the map is unaffected.

Two deviations from tilda-geo are patched locally
([scripts/patch_osmnexus_config.py](scripts/patch_osmnexus_config.py), applied by
`fetch_osmnexus.sh`):

- tilda-geo never splits a path-like way (path, footway, cycleway, track, steps,
  pedestrian) into left/right objects, while OSMnexus v0.1.0 does as soon as it has
  a `cycleway:*` tag. A segregated path tagged `cycleway:surface=asphalt` then got a
  dashed "Führungsform unklar" line on each side — about 4,300 objects and 480 km in
  Berlin's newest snapshot, nearly the whole category.
- tilda-geo drops ways that cycling may not use before classifying them: `access`
  private / no / delivery / permit (the same values in `bicycle` on a cycleway or
  `foot` on a footway) and service roads other than alleys unless they allow bikes.
  OSMnexus keeps them, so for example the fenced practice tracks of a
  Jugendverkehrsschule showed up. About 135 objects in the newest snapshot.

**Versions instead of one tileset per date.** Consecutive snapshots are compared per
object (a way, or one side of a road: `way/123/cycleway/left`). A new version
starts whenever category, geometry, name or directions of travel (see Lengths)
change, and ends when the object disappears. Each version is tiled once, with

- `f` — first snapshot it is part of (`YYYYMMDD`)
- `t` — first snapshot it is no longer part of, or `99991231`

The state at snapshot `D` is exactly `f <= D < t`. The page keeps `D` in MapLibre's
[global state](https://maplibre.org/maplibre-style-spec/expressions/#global-state)
and every layer filters on it, so moving the slider changes one value and loads
nothing. An unchanged cycleway is one feature however many snapshots it spans.
[tests/test_versions.py](tests/test_versions.py) checks that the filter reproduces
every snapshot exactly.

A moved node or a new name is a new version, but not a new kind of infrastructure;
about half of all versions are such edits. So each version also carries the span
of its category, which the popup shows instead of `f` and `t`:

- `cf`, `ct` — first snapshot of the unbroken run of this category on this object,
  and the first one after it
- `pc`, `nc` — the category in the snapshot before and after that run, if the
  object was drawn there

These four are not in the tiles. The build's `spans.bin` holds them as six
bytes per version at offset `vid × 6` (`cf`, `ct` as snapshot indices, `pc`, `nc`
as indices into the timeline's `categories`), and a click reads its six bytes with
one range request. Without them the detail tiles are about a seventh smaller.

No feature is dropped at any zoom (`--no-feature-limit --no-tile-size-limit`).
tippecanoe's usual `--drop-densest-as-needed` would thin out versions unevenly, and
streets would flicker in and out while scrubbing.

**Two archives, as MapLibre Tiles.** A date change makes MapLibre re-filter every
loaded tile, so overview tiles are kept light: `berlin_lo.pmtiles` (z9–11) has a
layer `bikelanes_lo` with only category, side and lifespan, and versions with equal
values merged into one feature per tile (`--reorder --coalesce`).
`berlin.pmtiles` (z12–14) has every version in `bikelanes`, with the id and name
that hover and popup need. Both are [MLT](https://github.com/maplibre/maplibre-tile-spec)
(`--tile-format=mlt`, read by MapLibre GL JS 5.12 and later with
`encoding: 'mlt'`), about a quarter smaller than the same tiles as MVT. manby
cannot join MLT archives, hence two files and two sources; the page draws each
category from both and switches at z12.

**On the page,** dragging the timeline or holding a key moves readout, legend and
chart at once, but the map only takes the newest date: once the input rests for
120 ms, at least every 450 ms while it keeps moving, and never while the previous
date is still being drawn. Playing waits for each step to be drawn. The hover halo
is a feature-state, not a filter. Timings in [site/config.js](site/config.js).

**Lengths** are counted per side of the road and per way: a street with cycle
tracks on both sides counts twice, a two-way path of its own once. Categories the
details style does not draw (`cyclewayLink`) are left out, as on radinfra.de's map.

radinfra.de's Statistik counts per direction of travel instead
(`tilda_measure_directions` in tilda-geo): a line that is not a oneway counts twice.
Every version records that as `dirs`, and counted that way Berlin's newest
snapshot has 3,588 km against 3,548 km on radinfra.de (November 2026). Over the
years, though, that count follows the direction tags more than the cycleways. Old
data mostly lacks them — in 2016, 43 of 4,919 Berlin roads with `…=track` had a
`:oneway` — and without one radinfra.de takes a track to run both ways, so
`cycleway:both=track` counted four times. Berlin would fall from 4,294 km (2016) to
3,588 km, and "Getrennter Radweg" from 2,584 km to 642 km, mostly because
directions were added and tracks redrawn as one-way ways of their own.
`COUNT_DIRECTIONS` in [pipeline/versions.py](pipeline/versions.py) switches to that
count; it changes the km only, not the versions or the tiles.

**Aerial photos.** For Berlin the background can be switched to the Geoportal's
orthophotos (2017–2026, via the codefor.de mirror). For each snapshot the newest
photo taken on or before that date is shown, which is what mappers could have seen
at the time; photos dated only by year count from 1 January. The capture list,
[site/data/berlin_imagery.json](site/data/berlin_imagery.json), is generated from
the [Editor Layer Index](https://github.com/osmlab/editor-layer-index) with
[@osm-editor-kit/maplibre-editor-layer-index](https://github.com/osm-editor-kit/maplibre-editor-layer-index):

```bash
cd scripts/imagery && npm ci && npm run build
```

**Areas.** The numbers on the page — legend, trend lines, timeline, table and the
change table — are for an area: Berlin as a whole by default (inside the state
boundary, so without the Brandenburg edges of the extract), or one of the 12 Bezirke
and 97 Ortsteile, picked from the list or by clicking on the map. The boundaries are
OSM's own (admin_level 4, 9 and 10) from the newest snapshot, used for every date.
A line crossing a boundary counts with its part inside. The change table compares
any snapshot with the one selected on the timeline; `?area=kreuzberg` links to an
area.

## What the map shows and what it does not

- **What OSM knew, not what was built.** Mapping campaigns show up as jumps.
- **Today's rules on old data.** Every snapshot is classified with the current
  rules, which keeps the method constant, but older tagging lands more often in
  "Führungsform unklar".
- When a cycleway moves from tags on the road to a way of its own, its line moves
  a few metres. That is a change in mapping, not new infrastructure.
- Snapshot granularity: a change is dated to the snapshot it first appears in; the
  popup says between which two snapshots it happened.
- The dip from 2021 to 2024 (about −400 km for the extract, marked in the
  timeline) is mostly a change of mapping, not of the streets. Checked by object
  and in a sample of the ways' tags: on 533 km of road sides the track tag went,
  mostly `cycleway:*=track` → `separate`, and the new ways of their own cover
  about 90 % of that, but at first often without full tags (around 170 km
  "Führungsform unklar" in mid 2024); and 445 km of "Gehweg, Rad frei" went because
  `bicycle=yes` was removed from footways (about 70 %) or set to `no`/`dismount`.
  Double counting (a road tag and a way of its own for the same track) was rare:
  at most about 50 km. So there is nothing to subtract; the band says what happened.
- The popup dates the category, not the line: "since" is when this side of the way
  got its current category, whatever was edited on it later. A way that was split
  continues under new ids, so its history starts over there ("nicht unter dieser
  Way-ID erfasst").

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

uv run python -m pipeline all --osh berlin-internal.osh.pbf --start 2016-01-01 --step 3
uv run python scripts/serve.py         # http://127.0.0.1:8765
```

To work on the page without building, `./scripts/fetch_data.sh` downloads the
published build instead. Locally the page reads `site/data/`; with `?data=remote`
it reads the bucket.

Each step also runs on its own (`snapshots`, `classify`, `versions`, `areas`, `tiles`);
classifications are reused unless `--force`. From a history file, `all` and
`classify` cut one snapshot at a time, classify it and delete it again (all but the
newest, for the area boundaries; `--keep-snapshots` keeps them), so a run that
stops carries on where it was when started again. `--jobs N` works on N snapshots
at once; memory is the limit. `--region` picks one of the regions in
[pipeline/regions.py](pipeline/regions.py) (default `berlin`); each has its own
folders in `data/` and `site/data/`, and its timeline keeps the bounding box of the
history file, which an update's extract has to cover. Instead of a history file, dated
extracts work too, such as Geofabrik's yearly public `berlin-YYMMDD.osm.pbf`:

```bash
uv run python -m pipeline all --pbf 'data/dated/berlin-*.osm.pbf'
```

`scripts/serve.py` instead of `python -m http.server`, because PMTiles reads byte
ranges and the stock server ignores `Range`.

The link preview for Mastodon, Slack and the like (`site/og.png`, named in the
page's Open Graph tags) is drawn from a build by `uv run python scripts/og_image.py`;
it needs `rsvg-convert` and the Inter font.

Berlin, 45 snapshots on a 2-core machine: about 15 s per snapshot to cut and 10 s to
classify.

## Publishing

GitHub Pages serves the page only
([.github/workflows/pages.yml](.github/workflows/pages.yml)). The data is in the
public B2 bucket `radinfra-history`, behind Cloudflare at
`https://tiles.vizsim.de/file/radinfra-history/`, in the same layout as
`site/data/`:

```
berlin/timeline.json              Cache-Control: no-cache — names the current build
berlin/builds/<id>/…              Cache-Control: public, max-age=31536000, immutable
```

A build's id is a hash of all its files, so a path always means the same bytes:
browsers and Cloudflare may keep them for good, and a page never combines tiles
and popup dates of two builds. The build also holds `versions.parquet`, the state
the [updates](#updates) continue from; the page never loads it.

```bash
./scripts/publish_b2.sh berlin        # the build, then the timeline; old builds deleted
./scripts/fetch_data.sh berlin        # the published build into site/data/berlin/
```

`publish_b2.sh` takes the key from `B2_APPLICATION_KEY_ID` and
`B2_APPLICATION_KEY`, or from `.env` (never committed), and keeps the current
build and the one before it. The bucket has a CORS rule for
`b2_download_file_by_name` with `Range`, and keeps only the newest version of a
file.

## Updates

[.github/workflows/update.yml](.github/workflows/update.yml) adds the newest state
of OSM on the 2nd of January, April, July and October: Geofabrik's public
`berlin-latest.osm.pbf`, which has no personal metadata, dated by its replication
timestamp. It is classified like every snapshot before it and appended to the
versions of the published build; areas, tiles and popup file are rebuilt, and the
new build is published. The history file is not needed again.

```bash
uv run python -m pipeline update --pbf berlin-latest.osm.pbf   # the same, by hand
```

Appending gives what a build from all snapshots gives, apart from the numbering of
new versions (tested, and checked on Berlin's 45 snapshots). An extract that is not
newer than the last snapshot changes nothing; one that covers less than the
snapshots before it, or lacks an area the page has, stops the update. Its log says
why versions end (gone, category, geometry, name, directions), how many changes lie
in Berlin, and how many of the ways with a new category were edited in OSM since
the last snapshot, by the extract's timestamps; a much smaller number there would
mean the extract changed, not OSM. The workflow
runs every month, because GitHub turns off the schedules of a public repository
after 60 days without activity; in the months between, it only renews the
schedule. Started by hand, it builds without publishing unless `publish` is ticked,
and keeps the result as an artifact either way.

## Credits

- Classification: [radinfra.de](https://radinfra.de) / [tilda-geo](https://github.com/FixMyBerlin/tilda-geo)
  by FixMyCity; style file `site/radinfra_details_style.json` taken from their tiles.
- [OSMnexus](https://github.com/rush42/OSMnexus) (AGPL-3.0), used as a binary with its `configs/tilda`.
- [manby](https://github.com/birkskyum/manby) for tiling, [osmium](https://osmcode.org/osmium-tool/) for snapshots.
- Aerial photos: Geoportal Berlin (Datenlizenz Deutschland, OSM permission), tiles from
  the codefor.de mirror, listed via the Editor Layer Index.
- Version history links go to [OSM Deep History](https://osmlab.github.io/osm-deep-history/).
- The page's look (colours, cards, headings, popups, tooltips) follows
  [vizsim/unfallkarte](https://github.com/vizsim/unfallkarte); layout and map
  interaction (hover halo, side offsets, links to radinfra.de) follow
  [vizsim/bikeneat](https://github.com/vizsim/bikeneat).
- Logo: a wheel that is also a clock, its rim a history arrow drawn as an OSM way in
  radinfra.de's category colours; wordmark outlined from Inter (SIL OFL).
  Generated by [scripts/logo.py](scripts/logo.py).
