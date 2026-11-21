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

Writes, into the build:

    areas.json      km per legend row and snapshot for every area
    areas.geojson   simplified boundaries for the map
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
from .versions import counted_m, geometry_lengths_m

# OSM's admin levels in Germany, as the page names them.
LEVELS = {"2": "Staat", "4": "Land", "6": "Kreis", "8": "Gemeinde", "9": "Bezirk", "10": "Ortsteil"}
PLURAL = {"Staat": "Staaten", "Land": "Länder", "Kreis": "Kreise", "Gemeinde": "Gemeinden",
          "Bezirk": "Bezirke", "Ortsteil": "Ortsteile"}
# ~5 m at Berlin's latitude: invisible at the zooms the outline is seen at.
SIMPLIFY_DEG = 0.00005
# Versions cut with the areas at once: bounds the memory for Germany.
LINE_CHUNK = 1_000_000


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


def build_areas(snapshot_pbf, versions_parquet, timeline_json, out_json, out_geojson, region, keep=()):
    """`region`: its entry in pipeline/regions.py. `keep`: slugs that must still
    be there (the quarterly update passes the current ones, so an extract with a
    broken boundary cannot drop an area)."""
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
    # What the newest snapshot changed, and how much of it in the region: a new
    # kind of extract would show up as changes outside, at its edge.
    changes = np.zeros(4, dtype=np.int64)  # start, end, start inside, end inside
    for first in range(1, last_vid + 1, LINE_CHUNK):
        cols = con.execute(
            f"SELECT category, k0, k1, dirs, length_m, geometry::BLOB AS wkb FROM read_parquet('{versions_parquet}') "
            "WHERE vid >= ? AND vid < ?", [first, first + LINE_CHUNK]
        ).fetchnumpy()
        lines = shapely.from_wkb(np.asarray([bytes(b) for b in cols["wkb"]], dtype=object))
        gi = np.array([group_of[c] for c in cols["category"]], dtype=np.int64)
        k0 = cols["k0"].astype(np.int64)
        k1 = cols["k1"].astype(np.int64)

        # Every (line, area) pair that touches; a line inside counts whole, one that
        # crosses the boundary only with its part inside.
        line_i, area_i = tree.query(lines, predicate="intersects")
        inside = shapely.contains_properly(polygons[area_i], lines[line_i])
        length = np.where(inside, cols["length_m"][line_i], 0.0)
        crossing = ~inside
        cut = shapely.intersection(lines[line_i[crossing]], polygons[area_i[crossing]])
        length[crossing] = geometry_lengths_m(cut)
        length = counted_m(length, cols["dirs"][line_i])

        np.add.at(diff, (area_i, gi[line_i], k0[line_i]), length)
        np.add.at(diff, (area_i, gi[line_i], k1[line_i] + 1), -length)

        in_region = np.zeros(len(lines), dtype=bool)
        in_region[line_i[area_i == 0]] = True
        starts, ends = k0 == n - 1, k1 == n - 2
        changes += [starts.sum(), ends.sum(), (starts & in_region).sum(), (ends & in_region).sum()]
    km = np.cumsum(diff, axis=2)[:, :, :n] / 1000.0

    out = {
        "snapshots": [s["key"] for s in timeline["snapshots"]],
        "groups": [g["id"] for g in groups],
        "areas": [],
    }
    if aliases:
        out["aliases"] = aliases
    for a, area in enumerate(areas):
        series = {}
        for g, group in enumerate(groups):
            values = np.round(km[a, g], 2)
            if values.any():
                series[group["id"]] = [float(v) for v in values]
        bounds = shapely.bounds(area["geom"])
        out["areas"].append({
            "slug": area["slug"], "name": area["name"], "level": area["level"],
            "parent": area["parent"], "osm": area["osm"],
            "km2": round(area_km2(area["geom"]), 1),
            "bbox": [round(float(v), 5) for v in bounds],
            "km": series,
        })
    Path(out_json).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n",
                              encoding="utf-8")

    features = []
    for area in areas:
        geom = shapely.set_precision(shapely.simplify(area["geom"], SIMPLIFY_DEG, preserve_topology=True), 1e-5)
        features.append({
            "type": "Feature",
            "properties": {"slug": area["slug"], "name": area["name"], "level": area["level"]},
            "geometry": json.loads(shapely.to_geojson(geom)),
        })
    Path(out_geojson).write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                            ensure_ascii=False, separators=(",", ":")) + "\n",
                                 encoding="utf-8")

    top = areas[0]
    top_now = sum(v[-1] for v in out["areas"][0]["km"].values())
    per_level = defaultdict(int)
    for area in areas[1:]:
        per_level[area["level"]] += 1
    print(f"  {len(areas)} areas ({', '.join(f'{c} {PLURAL[lv]}' for lv, c in per_level.items())}); "
          f"{top['name']} now {top_now:,.0f} km of {timeline['total_km'][-1]:,.0f} km in the extract", flush=True)
    print(f"  newest snapshot: {changes[0]:,} versions start, {changes[1]:,} end; "
          f"in {top['name']} {changes[2]:,} and {changes[3]:,}", flush=True)
    return out
