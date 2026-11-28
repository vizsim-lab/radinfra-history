<p align="center"><img src="site/logo-wordmark.svg" alt="radinfra-history" height="80"></p>

# radinfra-history

How the cycling infrastructure recorded in OpenStreetMap has changed over time,
classified the way [radinfra.de](https://radinfra.de) classifies it today, on one
map with a time slider.

**Germany, quarterly from January 2016 to the end of the history file (16 November
2026), with numbers for every Land, Kreis and Gemeinde** and, in Berlin, for every
Bezirk and Ortsteil. Berlin came first and is still updated every quarter from
Geofabrik's public extract (see [Updates](#updates)).

The map draws radinfra.de's own "details" style — same categories, colours and
dashes — against data computed here for every snapshot. Each side of a road is
its own line, offset to its side. Clicking a line shows since when it has been
in OSM in that form, with links to the way and its version history. Where a Land
or city offers them, the background can show the aerial photo that matches the
selected date.

Not a publication of radinfra.de or FixMyCity.

## How it works

```
germany-internal.osh.pbf                full history, with personal metadata
  │  osmium time-filter, every 3 months, metadata stripped, one at a time
  ▼
data/germany/snapshots/<date>.osm.pbf   the state of OSM at a date (deleted once classified)
  │  OSMnexus, configs/tilda (bikelanes topic)
  ▼
data/germany/classified/<date>.parquet  radinfra categories per way / side of a road
  │  pipeline/versions.py
  ▼                                     one build: site/data/germany/builds/<id>/
versions.parquet                        every distinct version once, with a lifespan
  │  pipeline/areas.py (OSM boundaries)     │  manby (tippecanoe-compatible), MLT
  ▼                                         ▼
areas/index.json                        tiles_far.pmtiles, tiles_lo.pmtiles, tiles.pmtiles
  every area: name, level, parent, box    z5–8, z9–11 and z12–14, for all dates
areas/<Kreis>.json                      spans.bin
  km per category, snapshot and area,     the popup's dates, 6 bytes per version
  and what changed in OSM per step
areas.pmtiles, outline.geojson          timeline.json
  the boundaries                          km per category and snapshot (whole extract)
```

The regions are named in [pipeline/regions.py](pipeline/regions.py): `germany`,
`berlin` and, small for trying things out, `saarland`. Every command from
`versions` on makes a new build ([pipeline/builds.py](pipeline/builds.py));
`site/data/<region>/timeline.json` points at the current one.

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

From z9 on no feature is dropped (`--no-feature-limit --no-tile-size-limit`).
tippecanoe's usual `--drop-densest-as-needed` would thin out versions unevenly, and
streets would flicker in and out while scrubbing.

**Three archives, as MapLibre Tiles.** A date change makes MapLibre re-filter every
loaded tile, so overview tiles are kept light: `tiles_lo.pmtiles` (z9–11) has a
layer `bikelanes_lo` with only category, side and lifespan, and versions with equal
values merged into one feature per tile (`--reorder --coalesce`). `tiles.pmtiles`
(z12–14) has every version in `bikelanes`, with the id and name that hover and
popup need. Below z9, for Germany seen whole, `tiles_far.pmtiles` (z5–8) leaves the
side out and the short versions too, as radinfra.de leaves lines under 100 m out
below z9: under 250 m at z5–6, 150 m at z7, 100 m at z8. That is 54 % of Germany's
versions but 8 % of its km, and keeps its largest tile at 1.6 MB instead of 3.1 MB;
a version is in or out by its own length, not by the date, so nothing flickers.
All three are [MLT](https://github.com/maplibre/maplibre-tile-spec)
(`--tile-format=mlt`, read by MapLibre GL JS 5.12 and later with
`encoding: 'mlt'`), about a quarter smaller than the same tiles as MVT. manby
cannot join MLT archives, hence three files and three sources; the page draws each
category from each and switches at z9 and z12. For Germany: 18, 53 and 223 MB.

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

**Aerial photos.** The background can be switched to the orthophotos of the Land
or city at the map's centre, a city's before its Land's (18 series; none for Bayern
statewide, Schleswig-Holstein and Bremen in the index). For each snapshot the newest
photo taken on or before that date is shown, which is what mappers could have seen
at the time; photos dated only by year count from 1 January. Older ones exist for
Berlin (2017–2026, Geoportal via the codefor.de mirror), Sachsen (from 2005),
Rheinland-Pfalz (from 2016), Mecklenburg-Vorpommern (from 2019) and some cities;
Länder that fly half their area a year have a coverage per capture. The lists,
[site/data/germany_imagery.json](site/data/germany_imagery.json) and
[site/data/berlin_imagery.json](site/data/berlin_imagery.json), are generated from
the [Editor Layer Index](https://github.com/osmlab/editor-layer-index) with
[@osm-editor-kit/maplibre-editor-layer-index](https://github.com/osm-editor-kit/maplibre-editor-layer-index).
Every capture is tried as a browser would load it; one without an image or a CORS
header is left out (Sachsen-Anhalt sends none), and one that draws only close up
gets that zoom (Hessen from about 14.5):

```bash
cd scripts/imagery && npm ci && npm run build
```

**Areas.** The numbers on the page — legend, trend lines, timeline, table and the
change table — are for an area: Germany as a whole by default (inside its boundary,
so without the extract's edges abroad), or one of 16 Länder, 400 Kreise, 10,833
Gemeinden, and in Berlin 12 Bezirke and 97 Ortsteile, found by typing part of a name
or picked on the map. The boundaries are OSM's own (admin_level 2, 4, 6, 8, and 9
and 10 in Berlin) from the newest snapshot, used for every date. A line crossing a
boundary counts with its part inside. A name several areas share is kept by the
highest of them, the others get their parent's slug (`aach-landkreis-konstanz`);
Berlin's old slugs still work (`?area=bezirk-pankow`, `?area=biesdorf`). The page
loads an index of all areas (0.4 MB compressed), an area's numbers with the file of
its Kreis when it is picked, and the boundaries as tiles. The change table compares
any snapshot with the one selected on the timeline.

**Changes in OSM.** The sum of a category can stay put while much changes
underneath, so the timeline has a bar per step for the km that changed in OSM since
the snapshot before, and its tooltip sets them against what that did to the sum.
They are counted by object, from the versions' category runs (`cf`, `ct`, `pc`,
`nc`): a way or road side whose legend row changed, one that got a category with
nothing drawn before it on that object (came), and one that lost it (went). Each
counts once per step; a change of category within a legend row, a moved node, a new
name or a longer line does not. The bars say nothing about the street, and nothing
about where an object lay before: a way redrawn under a new id, or a cycleway moved
from its road's tags to a way of its own, comes under the new id and goes under
the old one; a way split in two keeps its id on one part, which gets shorter, and
the other part comes. So the sum moves by what came, less what went, plus what the
objects that go on got longer or shorter, and the tooltip names that rest too. For
Berlin's extract since 2020: 2,993 km came, 2,172 km went, the objects that went on
got 988 km shorter, and the sum fell by 167 km; 1,582 km changed legend row. The
areas get them with their km (`act` in the bundles, see
[pipeline/areas.py](pipeline/areas.py)); builds from before November 2026 have none,
and the page then draws no bars.

With legend rows hidden, line and bars count the rows shown. For that the bundles
have the same numbers by legend row too: what came and went per row (`cw`), and
every change of row with the row it came from (`ch`). Seen from the rows shown, a
change from a hidden row is a gain and one to a hidden row a loss; for Germany in
the first quarter of 2022, with "Führungsform unklar" hidden, 1,155 km came from it
and 56 km went to it. That makes the bundles 40 % larger (Germany and its Länder,
loaded with the page, 163 instead of 34 KB compressed); builds from before 27
November 2026 have none, and the page then draws no bars while rows are hidden.

**What the map shows.** A switch above the total counts, instead of the area, the
part of the map not covered by panel and timeline
([site/viewport.js](site/viewport.js)). There is nothing to fetch for it: every tile
holds every version with its dates, so the tiles the map has loaded give the whole
timeline at once. Each line counts with its part inside the view and inside its own
tile (tiles repeat what crosses their edge), measured as the pipeline measures.
Against the pipeline's own numbers for the same box, the count is within 0.1 % from
the detail tiles (z12 and closer) and 0.5 % from the simplified overview tiles
(z9–11); below z9 the far tiles leave short lines out, so the switch counts from
z9 on. Tiles have no ids or previous categories at the overview zooms, so there are
no bars for a view. The first count after a zoom in Berlin or the Ruhr takes about
0.7 s, most of it MapLibre decoding the tiles again for the query; tiles wholly in
view are kept, so moving the map costs little more than the tiles on its edge.

## What the map shows and what it does not

- **What OSM knew, not what was built.** Mapping campaigns show up as jumps.
- **Today's rules on old data.** Every snapshot is classified with the current
  rules, which keeps the method constant, but older tagging lands more often in
  "Führungsform unklar".
- When a cycleway moves from tags on the road to a way of its own, its line moves
  a few metres. That is a change in mapping, not new infrastructure.
- Snapshot granularity: a change is dated to the snapshot it first appears in; the
  popup says between which two snapshots it happened.
- Such changes of mapping are regional. In Berlin, the dip from 2021 to 2024 (about
  −400 km for Berlin's extract, marked on the timeline while Berlin or a part of it
  is picked: `AREA_NOTES` in [site/config.js](site/config.js)) is mostly one, not a
  change of the streets. Checked by object
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

uv run python -m pipeline all --region germany --osh germany-internal.osh.pbf --threads 8
uv run python scripts/serve.py         # http://127.0.0.1:8765
```

To work on the page without building, `./scripts/fetch_data.sh germany` downloads
the published build instead. Locally the page reads `site/data/`, `?region=berlin`
shows another region built there, and `?data=remote` reads the bucket.

Each step also runs on its own (`snapshots`, `classify`, `versions`, `areas`, `tiles`);
classifications are reused unless `--force`. From a history file, `all` and
`classify` cut one snapshot at a time, classify it and delete it again (all but the
newest, for the area boundaries; `--keep-snapshots` keeps them), so a run that
stops carries on where it was when started again. `--jobs N` works on N snapshots
at once; memory is the limit. `--region` picks one of the regions in
[pipeline/regions.py](pipeline/regions.py) (default `berlin`, for the quarterly
update); each has its own
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
it needs `rsvg-convert` and the Inter font (`apt install librsvg2-bin fonts-inter`).

Germany, 45 snapshots from a 12.5 GB history file on 8 cores and 48 GB: 3 hours to
cut and classify (about 2½ min to cut and 1 min to classify a snapshot, at most
4.4 GB of memory, one 4.2 GB snapshot on disk at a time), then 6 minutes for
3.2 million versions, 11,359 areas and the tiles (at most 12 GB). Berlin on a
2-core machine: about 15 s per snapshot to cut and 10 s to classify.

## Publishing

GitHub Pages serves the page only
([.github/workflows/pages.yml](.github/workflows/pages.yml)). The data is in the
public B2 bucket `radinfra-history`, behind Cloudflare at
`https://tiles.vizsim.de/file/radinfra-history/`, in the same layout as
`site/data/`:

```
germany/timeline.json             Cache-Control: no-cache — names the current build
germany/builds/<id>/…             Cache-Control: public, max-age=31536000, immutable
berlin/…                          the same for Berlin
```

A build's id is a hash of all its files, so a path always means the same bytes:
browsers and Cloudflare may keep them for good, and a page never combines tiles
and popup dates of two builds. The build also holds `versions.parquet`, the state
the [updates](#updates) continue from; the page never loads it.

```bash
./scripts/publish_b2.sh germany       # the build, then the timeline; old builds deleted
./scripts/fetch_data.sh germany       # the published build into site/data/germany/
```

`publish_b2.sh` takes the key from `B2_APPLICATION_KEY_ID` and
`B2_APPLICATION_KEY`, or from `.env` (never committed), and keeps the current
build and the one before it. The bucket has a CORS rule for
`b2_download_file_by_name` with `Range`, and keeps only the newest version of a
file.

## Updates

[.github/workflows/update.yml](.github/workflows/update.yml) adds the newest state
of OSM on the 2nd of January, April, July and October. For now it does so for
Berlin; Germany's quarterly update is to follow, until then by hand
(`pipeline update --region germany` with `germany-latest.osm.pbf`). It takes
Geofabrik's public `berlin-latest.osm.pbf`, which has no personal metadata, dated
by its replication timestamp. It is classified like every snapshot before it and appended to the
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
- Aerial photos: the survey offices of the Länder and the cities named in the
  page's credit line, listed via the Editor Layer Index; Berlin's from the Geoportal
  (Datenlizenz Deutschland, OSM permission) via the codefor.de mirror.
- Version history links go to [OSM Deep History](https://osmlab.github.io/osm-deep-history/).
- The page's look (colours, cards, headings, popups, tooltips) follows
  [vizsim/unfallkarte](https://github.com/vizsim/unfallkarte); layout and map
  interaction (hover halo, side offsets, links to radinfra.de) follow
  [vizsim/bikeneat](https://github.com/vizsim/bikeneat).
- Logo: a wheel that is also a clock, its rim a history arrow drawn as an OSM way in
  radinfra.de's category colours; wordmark outlined from Inter (SIL OFL).
  Generated by [scripts/logo.py](scripts/logo.py).
