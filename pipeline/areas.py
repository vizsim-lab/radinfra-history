"""km per category, snapshot and area: the region and its administrative areas.

The areas are OSM's own boundaries, at the admin levels pipeline/regions.py
names for the region (Berlin: Bezirke and Ortsteile; Germany: Länder, Kreise and
Gemeinden, with Berlin's Bezirke and Ortsteile inside it), taken from the newest
snapshot. They are used for every date: they hardly change, and the question is
what happened inside a fixed area.

Every version is cut with every area it touches. A line that lies inside an area
counts with its full length; one that crosses the boundary counts with the part
inside, as radinfra.de's Statistik does. Lengths count as everywhere else
(versions.counted_m): per side of the road and per way.

Where the km change, by pipeline/changes.py (changes.parquet in the build), goes
in with them: each version's start and end split by its shares, with the part of a
line inside the area, so that an area's change splits like the region's.

Writes, into the build:

    areas/index.json      every area: name, level, parent, bundle, km², bbox
    areas/<bundle>.json   for the areas of a Kreis:
                          km   km per legend row and snapshot
                          chg  [step, row, replaced, added, new, replaced, reduced, gone]:
                               km that started at a step (snapshot index) as replaced,
                               added or new, and km that ended there as replaced,
                               reduced or gone; `row` indexes the index's `groups`
                          fl   [step, row, from row, km]: started as replaced, by the
                               legend row of what each line replaced most, where
                               that is another row
    areas.pmtiles         the boundaries as tiles (outline, dimming, picking)
    outline.geojson       the region's own outline, simplified
"""

import json
import re
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np
import shapely

from . import tools
from .style import legend_groups
from .tiles import run_manby
from .versions import OPEN_END, counted_m, geometry_lengths_m

# OSM's admin levels in Germany, as the page names them.
LEVELS = {"2": "Staat", "4": "Land", "6": "Kreis", "8": "Gemeinde", "9": "Bezirk", "10": "Ortsteil"}
PLURAL = {"Staat": "Staaten", "Land": "Länder", "Kreis": "Kreise", "Gemeinde": "Gemeinden",
          "Bezirk": "Bezirke", "Ortsteil": "Ortsteile"}
# ~5 m at Berlin's latitude: invisible at the zooms the outline is seen at.
SIMPLIFY_DEG = 0.00005
# Versions cut with the areas at once: bounds the memory for Germany.
LINE_CHUNK = 1_000_000
# Up to this many areas, all numbers are one file (Berlin's 110); above, a file
# per Kreis (Germany's 11,359 in 413).
BUNDLE_ALL_UP_TO = 500
# The boundary tiles: from Germany whole to street level (overzoomed beyond).
AREAS_LAYER = "areas"
AREAS_MINZOOM, AREAS_MAXZOOM = 4, 12
# ~100 m: the region's outline in the link preview.
OUTLINE_DEG = 0.001


def slugify(name):
    text = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def read_boundaries(pbf, levels=tuple(LEVELS)):
    """Administrative areas of the snapshot at these admin levels, as
    (osmium area id, properties, shapely geometry)."""
    with tempfile.TemporaryDirectory(prefix="areas-") as tmp:
        admin = Path(tmp) / "admin.osm.pbf"
        levelled = Path(tmp) / "levels.osm.pbf"
        seq = Path(tmp) / "admin.geojsonseq"
        tools.run([tools.osmium(), "tags-filter", pbf, "r/boundary=administrative",
                   "-o", admin, "--overwrite"])
        # Germany has far more levels than these (Ämter, Stadtteile, …): leave
        # them out before the polygons are built.
        tools.run([tools.osmium(), "tags-filter", admin, f"r/admin_level={','.join(levels)}",
                   "-o", levelled, "--overwrite"])
        tools.run([tools.osmium(), "export", levelled, "-f", "geojsonseq",
                   "--geometry-types=polygon", "--add-unique-id=type_id",
                   "-o", seq, "--overwrite"])
        areas = []
        with open(seq, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip().lstrip("\x1e")
                if not line:
                    continue
                feature = json.loads(line)
                props = feature["properties"]
                if props.get("boundary") != "administrative" or props.get("admin_level") not in levels:
                    continue
                if not props.get("name"):
                    continue
                areas.append((feature.get("id"), props, shapely.geometry.shape(feature["geometry"])))
    return areas


def area_km2(geom):
    """Area in km², in a local equirectangular projection (exact enough for a city)."""
    lat0 = np.radians(shapely.get_coordinates(shapely.centroid(geom))[0, 1])
    projected = shapely.transform(geom, lambda c: np.column_stack((
        c[:, 0] * 111.320 * np.cos(lat0), c[:, 1] * 110.574)))
    return float(shapely.area(projected))


def _base_slug(level, name):
    return f"bezirk-{slugify(name)}" if level == "9" else slugify(name)


def select_areas(boundaries, relation, levels, nested=()):
    """The region (its boundary relation) and the areas at `levels` inside it, top
    down, each with the smallest area above it that contains it as `parent`.
    `nested`: ((slug, levels), ...), more levels inside one of those areas.

    Slugs are the name, `bezirk-` before a Bezirk's. A name that several areas share
    is kept by the one highest up, if only one is; the others get their parent's
    slug added. Areas of a nested level keep the slug they have in a build of their
    own as an alias (`aliases`), where it is not some other area's slug."""
    top_id = f"a{2 * relation + 1}"  # osmium's id for the area of a relation
    tops = [b for b in boundaries if b[0] == top_id]
    if len(tops) != 1:
        raise SystemExit(f"the region's boundary (relation {relation}) is not an area in the snapshot")
    osm_id, props, geom = tops[0]
    top = {"osm": osm_id, "name": props["name"], "level": LEVELS[props["admin_level"]],
           "parent": None, "geom": geom, "depth": 0, "base": slugify(props["name"]), "nested": False}
    areas = [top]

    def add_levels(within, level_list, depth, is_nested):
        """Areas at each level inside `within`, with their parents among `chain`."""
        chain = [within]
        shapely.prepare(within["geom"])
        for level in level_list:
            found = [b for b in boundaries if b[1]["admin_level"] == level and b[0] != within["osm"]]
            points = shapely.point_on_surface(np.array([b[2] for b in found], dtype=object))
            inside = shapely.contains(within["geom"], points) if found else np.array([], dtype=bool)
            found = [b for b, ok in zip(found, inside) if ok]
            points = points[inside]
            depth += 1
            new = []
            for (osm_id, props, geom), point in sorted(zip(found, points), key=lambda x: (x[0][1]["name"], x[0][0])):
                # The smallest area above that contains it: the deepest in the chain.
                parent = next((a for a in reversed(chain[1:]) if a["geom"].contains(point)), within)
                new.append({"osm": osm_id, "name": props["name"], "level": LEVELS[level], "parent": parent,
                            "geom": geom, "depth": depth, "base": _base_slug(level, props["name"]),
                            "nested": is_nested})
            for area in new:
                shapely.prepare(area["geom"])
            areas.extend(new)
            # A level whose areas all lie in one parent area still has the chain above.
            chain = chain + new

    add_levels(top, levels, 0, False)
    for slug, more in nested:
        within = next((a for a in areas if a["base"] == slug), None)
        if within is None:
            raise SystemExit(f"no area {slug!r} for its nested levels {more}")
        add_levels(within, more, within["depth"], True)

    _assign_slugs(areas)
    aliases = {a["base"]: a["slug"] for a in areas
               if a["nested"] and a["slug"] != a["base"]
               and not any(b["slug"] == a["base"] for b in areas)}
    for area in areas:
        area["parent"] = area["parent"]["slug"] if area["parent"] else None
    return areas, aliases


def _assign_slugs(areas):
    by_base = defaultdict(list)
    for area in areas:
        by_base[area["base"]].append(area)
    suffixed = set()
    for group in by_base.values():
        highest = min(a["depth"] for a in group)
        keepers = [a for a in group if a["depth"] == highest]
        for area in group:
            if len(keepers) > 1 or area is not keepers[0]:
                suffixed.add(id(area))
    # Top down, so a parent's slug is final before its children use it.
    for area in sorted(areas, key=lambda a: a["depth"]):
        area["slug"] = (f"{area['base']}-{area['parent']['slug']}" if id(area) in suffixed
                        else area["base"])
    counts = defaultdict(int)
    for area in areas:
        counts[area["slug"]] += 1
    for area in areas:
        if counts[area["slug"]] > 1 and id(area) in suffixed:
            area["slug"] = f"{area['slug']}-{area['osm'].lstrip('a')}"
    slugs = [a["slug"] for a in areas]
    duplicates = {s for s in slugs if slugs.count(s) > 1}
    if duplicates:
        raise SystemExit(f"area slugs are not unique: {sorted(duplicates)}")


def build_areas(snapshot_pbf, versions_parquet, timeline_json, out_dir, region, keep=(), changes_parquet=None):
    """Into `out_dir` (a build's work folder). `region`: its entry in
    pipeline/regions.py. `keep`: slugs that must still
    be there (the quarterly update passes the current ones, so an extract with a
    broken boundary cannot drop an area). Without `changes_parquet`, no `chg` or `fl`."""
    timeline = json.loads(Path(timeline_json).read_text(encoding="utf-8"))
    n = len(timeline["snapshots"])
    groups = legend_groups()
    group_of = {c: i for i, g in enumerate(groups) for c in g["categories"]}

    areas, aliases = select_areas(read_boundaries(snapshot_pbf), region.relation, region.levels, region.nested)
    missing = set(keep) - {a["slug"] for a in areas} - set(aliases)
    if missing:
        raise SystemExit(f"{snapshot_pbf}: areas missing against the current data: {sorted(missing)}")
    polygons = np.array([a["geom"] for a in areas], dtype=object)
    shapely.prepare(polygons)
    tree = shapely.STRtree(polygons)

    con = duckdb.connect()
    last_vid = con.execute(f"SELECT coalesce(max(vid), 0) FROM read_parquet('{versions_parquet}')").fetchone()[0]
    diff = np.zeros((len(areas), len(groups), n + 1))
    split = changes_parquet is not None and Path(changes_parquet).is_file()
    if split:
        # km that start and end per area, legend row and step: replaced, added, new;
        # replaced, reduced, gone. Replaced starts also by the row they replaced.
        cells = len(areas) * len(groups) * (n + 1)
        moved = np.zeros((6, cells))
        flow_keys, flow_km = [], []
        con.execute("CREATE TEMP TABLE row_of (row_category VARCHAR, row_gi INTEGER)")
        con.executemany("INSERT INTO row_of VALUES (?, ?)", list(group_of.items()))
        joined = f"""LEFT JOIN (SELECT vid, s_rep, s_add, s_new, s_from, e_rep, e_red, e_gone
                               FROM read_parquet('{changes_parquet}')) c USING (vid)
                     LEFT JOIN row_of r ON r.row_category = c.s_from"""
        # Without a split (no start, or no end), a version counts as new or gone.
        extra = (", t, coalesce(s_rep, 0) AS s_rep, coalesce(s_add, 0) AS s_add, coalesce(s_new, 1) AS s_new, "
                 "coalesce(r.row_gi, -1) AS from_gi, "
                 "coalesce(e_rep, 0) AS e_rep, coalesce(e_red, 0) AS e_red, coalesce(e_gone, 1) AS e_gone")
    else:
        joined, extra = "", ""
    # What the newest snapshot changed, and how much of it in the region: a new
    # kind of extract would show up as changes outside, at its edge.
    changes = np.zeros(4, dtype=np.int64)  # start, end, start inside, end inside
    for first in range(1, last_vid + 1, LINE_CHUNK):
        cols = con.execute(
            f"SELECT category, k0, k1, dirs, length_m, geometry::BLOB AS wkb{extra} "
            f"FROM read_parquet('{versions_parquet}') {joined} "
            "WHERE vid >= ? AND vid < ?", [first, first + LINE_CHUNK]
        ).fetchnumpy()
        lines = shapely.from_wkb(np.asarray([bytes(b) for b in cols["wkb"]], dtype=object))
        gi = np.array([group_of[c] for c in cols["category"]], dtype=np.int64)
        k0 = cols["k0"].astype(np.int64)
        k1 = cols["k1"].astype(np.int64)

        # Every (line, area) pair that touches; a line inside counts whole, one that
        # crosses the boundary only with its part inside. The tree only finds the
        # candidates by their boxes: its own predicate test prepares the line, not the
        # area, and then walks all of Germany's or a Land's boundary for every line.
        # Tested with the prepared area first, the result is the same pairs.
        line_i, area_i = tree.query(lines)
        touch = shapely.intersects(polygons[area_i], lines[line_i])
        line_i, area_i = line_i[touch], area_i[touch]
        inside = shapely.contains_properly(polygons[area_i], lines[line_i])
        length = np.where(inside, cols["length_m"][line_i], 0.0)
        crossing = ~inside
        cut = shapely.intersection(lines[line_i[crossing]], polygons[area_i[crossing]])
        length[crossing] = geometry_lengths_m(cut)
        length = counted_m(length, cols["dirs"][line_i])

        np.add.at(diff, (area_i, gi[line_i], k0[line_i]), length)
        np.add.at(diff, (area_i, gi[line_i], k1[line_i] + 1), -length)
        if split:
            cell = (area_i * len(groups) + gi[line_i]) * (n + 1)
            starts = k0[line_i] >= 1
            ends = np.asarray(cols["t"])[line_i] != OPEN_END
            for j, (name, rows, step) in enumerate((
                    ("s_rep", starts, k0), ("s_add", starts, k0), ("s_new", starts, k0),
                    ("e_rep", ends, k1 + 1), ("e_red", ends, k1 + 1), ("e_gone", ends, k1 + 1))):
                share = np.asarray(cols[name], dtype=float)[line_i]
                moved[j] += np.bincount((cell + step[line_i])[rows], weights=(length * share)[rows], minlength=cells)
            replaced = np.asarray(cols["s_rep"], dtype=float)[line_i]
            from_gi = np.asarray(cols["from_gi"], dtype=np.int64)[line_i]
            # Between legend rows only: within one, a replaced line is an edit.
            take = starts & (replaced > 0) & (from_gi >= 0) & (from_gi != gi[line_i])
            if take.any():
                flow_keys.append((cell[take] + k0[line_i][take]) * len(groups) + from_gi[take])
                flow_km.append(length[take] * replaced[take])

        in_region = np.zeros(len(lines), dtype=bool)
        in_region[line_i[area_i == 0]] = True
        starts, ends = k0 == n - 1, k1 == n - 2
        changes += [starts.sum(), ends.sum(), (starts & in_region).sum(), (ends & in_region).sum()]
    km = np.cumsum(diff, axis=2)[:, :, :n] / 1000.0

    numbers = []
    for a in range(len(areas)):
        series = {}
        for g, group in enumerate(groups):
            values = np.round(km[a, g], 2) + 0.0  # no -0.0 where a category empties
            if values.any():
                series[group["id"]] = [float(v) for v in values]
        numbers.append({"km": series})
    if split:
        _add_split(numbers, moved, flow_keys, flow_km, len(groups), n)
    index = _write(Path(out_dir), areas, aliases, numbers, timeline, groups)

    top = areas[0]
    top_now = sum(v[-1] for v in numbers[0]["km"].values())
    per_level = defaultdict(int)
    for area in areas[1:]:
        per_level[area["level"]] += 1
    print(f"  {len(areas)} areas ({', '.join(f'{c} {PLURAL[lv]}' for lv, c in per_level.items())}); "
          f"{top['name']} now {top_now:,.0f} km of {timeline['total_km'][-1]:,.0f} km in the extract", flush=True)
    print(f"  newest snapshot: {changes[0]:,} versions start, {changes[1]:,} end; "
          f"in {top['name']} {changes[2]:,} and {changes[3]:,}", flush=True)
    return index


def _add_split(numbers, moved, flow_keys, flow_km, n_groups, n):
    """`chg` and `fl` for every area (module docstring), in km to 10 m."""
    moved = np.round(moved / 1000.0, 2).reshape(6, len(numbers), n_groups, n + 1)
    for a, entry in enumerate(numbers):
        cells = moved[:, a]
        rows, steps = np.nonzero(np.abs(cells).sum(axis=0))
        entry["chg"] = sorted([int(k), int(r), *(float(v) for v in cells[:, r, k])] for r, k in zip(rows, steps))
        entry["fl"] = []
    if not flow_keys:
        return
    keys, inverse = np.unique(np.concatenate(flow_keys), return_inverse=True)
    km = np.round(np.bincount(inverse, weights=np.concatenate(flow_km)) / 1000.0, 2)
    keys, km = keys[km != 0], km[km != 0]
    cell, source = np.divmod(keys, n_groups)
    area, rest = np.divmod(cell, n_groups * (n + 1))
    row, step = np.divmod(rest, n + 1)
    for a, k, r, f, v in zip(area, step, row, source, km):
        numbers[a]["fl"].append([int(k), int(r), int(f), float(v)])
    for entry in numbers:
        entry["fl"].sort()


def _bundles(areas):
    """Which file holds each area's numbers: one for a small region; for a large
    one the area's Kreis (or Berlin's Bezirk), with its Gemeinden (Ortsteile), and
    one for the region and its Länder (`_top`)."""
    if len(areas) <= BUNDLE_ALL_UP_TO:
        return ["_all"] * len(areas)
    by_slug = {a["slug"]: a for a in areas}
    bundles = []
    for area in areas:
        node = area
        while node["depth"] > 2:
            node = by_slug[node["parent"]]
        bundles.append(node["slug"] if node["depth"] == 2 else "_top")
    return bundles


def _write(out_dir, areas, aliases, numbers, timeline, groups):
    """areas/index.json, areas/<bundle>.json, areas.pmtiles and outline.geojson."""
    folder = out_dir / "areas"
    folder.mkdir(parents=True, exist_ok=True)
    bundles = _bundles(areas)
    index = {
        "snapshots": [s["key"] for s in timeline["snapshots"]],
        "groups": [g["id"] for g in groups],
        "fields": ["slug", "name", "level", "parent", "bundle", "km2", "bbox"],
        "areas": [[a["slug"], a["name"], a["level"], a["parent"], bundle, round(area_km2(a["geom"]), 1),
                   [round(float(v), 5) for v in shapely.bounds(a["geom"])]]
                  for a, bundle in zip(areas, bundles)],
        "aliases": aliases,
    }
    _write_json(folder / "index.json", index)
    contents = defaultdict(dict)
    for area, bundle, entry in zip(areas, bundles, numbers):
        contents[bundle][area["slug"]] = entry
    for bundle, entries in contents.items():
        _write_json(folder / f"{bundle}.json", {"areas": entries})

    # The boundaries as tiles, for outline, dimming and picking on the map;
    # manby simplifies them per zoom and keeps shared borders shared.
    source = out_dir / ".areas.geojson"
    try:
        features = [{
            "type": "Feature",
            "properties": {"slug": a["slug"], "name": a["name"], "level": a["level"], "parent": a["parent"] or ""},
            "geometry": json.loads(shapely.to_geojson(
                shapely.set_precision(shapely.simplify(a["geom"], SIMPLIFY_DEG, preserve_topology=True), 1e-6))),
        } for a in areas]
        _write_json(source, {"type": "FeatureCollection", "features": features})
        run_manby(out_dir / "areas.pmtiles", AREAS_LAYER, AREAS_MINZOOM, AREAS_MAXZOOM,
                  ["slug", "name", "level", "parent"],
                  ["--detect-shared-borders", "--no-tiny-polygon-reduction"], source)
    finally:
        source.unlink(missing_ok=True)

    # The region's own outline, small, for the link preview (scripts/og_image.py).
    outline = shapely.set_precision(shapely.simplify(areas[0]["geom"], OUTLINE_DEG, preserve_topology=True), 1e-4)
    _write_json(out_dir / "outline.geojson", {"type": "Feature", "properties": {"name": areas[0]["name"]},
                                              "geometry": json.loads(shapely.to_geojson(outline))})
    sizes = {p.name: p.stat().st_size for p in [folder / "index.json", out_dir / "areas.pmtiles"]}
    print(f"  {len(contents)} bundles; " + ", ".join(f"{k} {v / 1e6:.1f} MB" for k, v in sizes.items()), flush=True)
    return index


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def current_slugs(build_dir):
    """The areas a build has, so an update can insist they are still there."""
    build_dir = Path(build_dir)
    index = build_dir / "areas" / "index.json"
    if index.is_file():
        data = json.loads(index.read_text(encoding="utf-8"))
        return [row[0] for row in data["areas"]]
    legacy = build_dir / "areas.json"  # builds from before November 2026
    if legacy.is_file():
        return [a["slug"] for a in json.loads(legacy.read_text(encoding="utf-8"))["areas"]]
    return []
