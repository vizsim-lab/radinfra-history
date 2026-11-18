"""km per category, snapshot and area: Berlin, its Bezirke and Ortsteile.

The boundaries come from OSM itself (admin_level 4 = Berlin, 9 = Bezirk,
10 = Ortsteil) and are taken from the newest snapshot. They are used for every
date: they hardly change, and the question is what happened inside a fixed area.

Every version is cut with every area it touches. A line that lies inside an area
counts with its full length; one that crosses the boundary counts with the part
inside, as radinfra.de's Statistik does. Lengths count as everywhere else
(versions.counted_m): per side of the road and per way.

Writes, next to the timeline:

    <slug>_areas.json      km per legend row and snapshot for every area
    <slug>_areas.geojson   simplified boundaries for the map
"""

import json
import re
import tempfile
import unicodedata
from pathlib import Path

import duckdb
import numpy as np
import shapely

from . import tools
from .style import legend_groups
from .versions import counted_m, geometry_lengths_m

LEVELS = {"4": "Land", "9": "Bezirk", "10": "Ortsteil"}
# ~5 m at Berlin's latitude: invisible at the zooms the outline is seen at.
SIMPLIFY_DEG = 0.00005


def slugify(name):
    text = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def read_boundaries(pbf):
    """Administrative areas of the snapshot as (properties, shapely geometry)."""
    with tempfile.TemporaryDirectory(prefix="areas-") as tmp:
        admin = Path(tmp) / "admin.osm.pbf"
        seq = Path(tmp) / "admin.geojsonseq"
        tools.run([tools.osmium(), "tags-filter", pbf, "r/boundary=administrative",
                   "-o", admin, "--overwrite"])
        tools.run([tools.osmium(), "export", admin, "-f", "geojsonseq",
                   "--geometry-types=polygon", "--add-unique-id=type_id",
                   "-o", seq, "--overwrite"])
        areas = []
        for line in seq.read_text(encoding="utf-8").splitlines():
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            feature = json.loads(line)
            props = feature["properties"]
            if props.get("boundary") != "administrative" or props.get("admin_level") not in LEVELS:
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


def select_berlin(boundaries):
    """Berlin itself and the Bezirke and Ortsteile inside it, with their Bezirk."""
    states = [b for b in boundaries if b[1]["admin_level"] == "4" and b[1]["name"] == "Berlin"]
    if len(states) != 1:
        raise SystemExit(f"expected one Berlin boundary (admin_level 4), found {len(states)}")
    berlin = states[0][2]
    shapely.prepare(berlin)
    inside = [b for b in boundaries if b[1]["admin_level"] in ("9", "10")
              and berlin.contains(shapely.point_on_surface(b[2]))]

    bezirke = [b for b in inside if b[1]["admin_level"] == "9"]
    areas = [{
        "osm": states[0][0], "slug": "berlin", "name": "Berlin", "level": LEVELS["4"],
        "parent": None, "geom": berlin,
    }]
    for osm_id, props, geom in sorted(bezirke, key=lambda b: b[1]["name"]):
        areas.append({"osm": osm_id, "slug": f"bezirk-{slugify(props['name'])}", "name": props["name"],
                      "level": LEVELS["9"], "parent": "berlin", "geom": geom})
    for osm_id, props, geom in sorted((b for b in inside if b[1]["admin_level"] == "10"),
                                      key=lambda b: b[1]["name"]):
        point = shapely.point_on_surface(geom)
        parent = next((a for a in areas[1:len(bezirke) + 1] if a["geom"].contains(point)), None)
        areas.append({"osm": osm_id, "slug": slugify(props["name"]), "name": props["name"],
                      "level": LEVELS["10"], "parent": parent["slug"] if parent else "berlin",
                      "geom": geom})

    slugs = [a["slug"] for a in areas]
    duplicates = {s for s in slugs if slugs.count(s) > 1}
    if duplicates:
        raise SystemExit(f"area slugs are not unique: {sorted(duplicates)}")
    return areas


def build_areas(snapshot_pbf, versions_parquet, timeline_json, out_json, out_geojson, keep=()):
    """`keep`: slugs that must still be there (the quarterly update passes the
    current ones, so an extract with a broken boundary cannot drop an area)."""
    timeline = json.loads(Path(timeline_json).read_text(encoding="utf-8"))
    n = len(timeline["snapshots"])
    groups = legend_groups()
    group_of = {c: i for i, g in enumerate(groups) for c in g["categories"]}

    areas = select_berlin(read_boundaries(snapshot_pbf))
    missing = set(keep) - {a["slug"] for a in areas}
    if missing:
        raise SystemExit(f"{snapshot_pbf}: areas missing against the current data: {sorted(missing)}")
    polygons = np.array([a["geom"] for a in areas], dtype=object)
    shapely.prepare(polygons)

    con = duckdb.connect()
    cols = con.execute(
        f"SELECT category, k0, k1, dirs, length_m, geometry::BLOB AS wkb FROM read_parquet('{versions_parquet}')"
    ).fetchnumpy()
    lines = shapely.from_wkb(np.asarray([bytes(b) for b in cols["wkb"]], dtype=object))
    gi = np.array([group_of[c] for c in cols["category"]], dtype=np.int64)
    k0 = cols["k0"].astype(np.int64)
    k1 = cols["k1"].astype(np.int64)

    # Every (line, area) pair that touches; a line inside counts whole, one that
    # crosses the boundary only with its part inside.
    tree = shapely.STRtree(polygons)
    line_i, area_i = tree.query(lines, predicate="intersects")
    inside = shapely.contains_properly(polygons[area_i], lines[line_i])
    length = np.where(inside, cols["length_m"][line_i], 0.0)
    crossing = ~inside
    cut = shapely.intersection(lines[line_i[crossing]], polygons[area_i[crossing]])
    length[crossing] = geometry_lengths_m(cut)
    length = counted_m(length, cols["dirs"][line_i])

    diff = np.zeros((len(areas), len(groups), n + 1))
    np.add.at(diff, (area_i, gi[line_i], k0[line_i]), length)
    np.add.at(diff, (area_i, gi[line_i], k1[line_i] + 1), -length)
    km = np.cumsum(diff, axis=2)[:, :, :n] / 1000.0

    out = {
        "snapshots": [s["key"] for s in timeline["snapshots"]],
        "groups": [g["id"] for g in groups],
        "areas": [],
    }
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

    berlin_now = sum(v[-1] for v in out["areas"][0]["km"].values())
    print(f"  {len(areas)} areas ({sum(a['level'] == 'Bezirk' for a in areas)} Bezirke, "
          f"{sum(a['level'] == 'Ortsteil' for a in areas)} Ortsteile); "
          f"Berlin now {berlin_now:,.0f} km of {timeline['total_km'][-1]:,.0f} km in the extract", flush=True)
    # What the newest snapshot changed, and how much of it in Berlin: a new kind of
    # extract would show up as changes outside, at its edge.
    in_berlin = np.zeros(len(lines), dtype=bool)
    in_berlin[line_i[area_i == 0]] = True
    starts, ends = k0 == n - 1, k1 == n - 2
    print(f"  newest snapshot: {starts.sum():,} versions start, {ends.sum():,} end; "
          f"in Berlin {(starts & in_berlin).sum():,} and {(ends & in_berlin).sum():,}", flush=True)
    return out
