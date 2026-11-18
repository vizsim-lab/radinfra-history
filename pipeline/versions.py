"""Turn per-snapshot classifications into versions with a lifespan.

An object (a way, or one side of a road) gets a new version whenever its
category, its geometry or its name changes between two snapshots, and a version
ends when the object disappears. Each version is stored once, with

    f   first snapshot it appears in        (YYYYMMDD)
    t   first snapshot it no longer appears in, or 99991231 if it still exists

so the state at snapshot date D is exactly the versions with f <= D < t. The map
filters on that, which is why one tileset can serve every point in time and an
unchanged cycleway costs one feature however many snapshots it spans.

Alongside, km per legend row and snapshot go to a small JSON for the timeline.
"""

import json
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import shapely

from .classify import date_key
from .style import drawn_categories, legend_groups

OPEN_END = 99991231
EARTH_RADIUS_M = 6_371_008.8


def lengths_m(wkb):
    """Great-circle length in metres of each WKB (Multi)LineString."""
    # DuckDB hands BLOBs back as bytearray; shapely wants bytes.
    return geometry_lengths_m(shapely.from_wkb(np.asarray([bytes(b) for b in wkb], dtype=object)))


def _explode(geoms):
    """Single parts of each geometry, through nested multi-parts and collections."""
    parts, index = shapely.get_parts(geoms, return_index=True)
    while True:
        nested = shapely.get_num_geometries(parts) > 1
        nested |= np.isin(shapely.get_type_id(parts), [4, 5, 6, 7])
        if not nested.any():
            return parts, index
        sub, sub_index = shapely.get_parts(parts, return_index=True)
        parts, index = sub, index[sub_index]


def geometry_lengths_m(geoms):
    """Great-circle length in metres of each geometry's lines (points count zero)."""
    geoms = np.asarray(geoms, dtype=object)
    parts, part_index = _explode(geoms)
    coords, coord_index = shapely.get_coordinates(parts, return_index=True)
    if len(coords) < 2:
        return np.zeros(len(geoms))
    lon = np.radians(coords[:, 0])
    lat = np.radians(coords[:, 1])
    same = coord_index[1:] == coord_index[:-1]
    dlat = lat[1:] - lat[:-1]
    dlon = lon[1:] - lon[:-1]
    a = np.sin(dlat / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2) ** 2
    segment = 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    per_part = np.bincount(coord_index[1:][same], weights=segment[same], minlength=len(parts))
    return np.bincount(part_index, weights=per_part, minlength=len(geoms))


def build_versions(classified, out_parquet, timeline_json, region=None):
    """classified: [(date, parquet)] oldest first. Writes versions + timeline."""
    classified = sorted(classified)
    dates = [d for d, _ in classified]
    if len(set(dates)) != len(dates):
        raise ValueError("duplicate snapshot dates")
    keys = [date_key(d) for d in dates]
    out_parquet = Path(out_parquet)
    out_parquet.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE snaps (k INTEGER, date INTEGER)")
    con.executemany("INSERT INTO snaps VALUES (?, ?)", list(enumerate(keys)))
    con.execute("CREATE TEMP TABLE drawn (category VARCHAR)")
    con.executemany("INSERT INTO drawn VALUES (?)", [[c] for c in sorted(drawn_categories())])

    files = [str(p) for _, p in classified]
    # Categories the radinfra style does not draw (cyclewayLink) are left out of
    # the tiles and the counts, like on radinfra.de's own map.
    con.execute("""
        CREATE TEMP TABLE obs AS
        SELECT s.k, c.id, c.osm_id, c.category, c.side, c.name,
               md5(c.geometry::BLOB) AS gh, c.geometry
        FROM read_parquet(?) c
        JOIN snaps s USING (date)
        WHERE c.category IN (SELECT category FROM drawn)
    """, [files])

    # Gaps and islands: within one (id, category, geometry, name), consecutive
    # snapshots share k - row_number(); a gap or a change starts a new island.
    con.execute(f"""
        CREATE TEMP TABLE versions AS
        WITH islands AS (
            SELECT *, k - row_number() OVER (
                PARTITION BY id, category, gh, name ORDER BY k) AS island
            FROM obs
        ), runs AS (
            SELECT id, category, gh, name,
                   min(k) AS k0, max(k) AS k1,
                   any_value(osm_id) AS osm_id, any_value(side) AS side,
                   any_value(geometry) AS geometry
            FROM islands
            GROUP BY id, category, gh, name, island
        )
        SELECT row_number() OVER (ORDER BY r.id, r.k0)::INTEGER AS vid,
               r.id, r.osm_id, r.category, r.side, r.name,
               sf.date AS f, coalesce(st.date, {OPEN_END}) AS t,
               r.k0, r.k1, r.geometry
        FROM runs r
        JOIN snaps sf ON sf.k = r.k0
        LEFT JOIN snaps st ON st.k = r.k1 + 1
    """)

    # A version must never overlap another of the same object.
    overlaps = con.execute("""
        SELECT count(*) FROM versions a JOIN versions b
          ON a.id = b.id AND a.vid < b.vid AND a.k0 <= b.k1 AND b.k0 <= a.k1
    """).fetchone()[0]
    if overlaps:
        raise SystemExit(f"{overlaps} overlapping versions — an object had two states at once")

    vid, category, k0, k1, wkb = con.execute(
        "SELECT vid, category, k0, k1, geometry::BLOB FROM versions ORDER BY vid"
    ).fetchnumpy().values()
    length = lengths_m(wkb)
    con.execute("CREATE TEMP TABLE lengths (vid INTEGER, length_m DOUBLE)")
    con.execute("INSERT INTO lengths SELECT * FROM (SELECT unnest(?) AS vid, unnest(?) AS length_m)",
                [vid.tolist(), length.round(1).tolist()])

    con.execute(f"""
        COPY (
            SELECT v.vid, v.id, v.osm_id, v.category, v.side, v.name, v.f, v.t,
                   v.k0, v.k1, l.length_m, v.geometry
            FROM versions v JOIN lengths l USING (vid)
            ORDER BY v.vid
        ) TO '{out_parquet}' (FORMAT parquet, COMPRESSION zstd)
    """)

    timeline = _timeline(dates, keys, category, k0, k1, length, con, region)
    timeline_json = Path(timeline_json)
    timeline_json.parent.mkdir(parents=True, exist_ok=True)
    timeline_json.write_text(json.dumps(timeline, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    n_versions = len(vid)
    n_objects = con.execute("SELECT count(DISTINCT id) FROM versions").fetchone()[0]
    print(f"  {n_versions:,} versions of {n_objects:,} objects over {len(dates)} snapshots", flush=True)
    return timeline


def _timeline(dates, keys, category, k0, k1, length, con, region):
    """km per legend row and snapshot, by a difference array over each lifespan."""
    n = len(dates)
    groups = legend_groups()
    group_of = {c: i for i, g in enumerate(groups) for c in g["categories"]}
    gi = np.array([group_of[c] for c in category], dtype=np.int64)

    diff = np.zeros((len(groups), n + 1))
    np.add.at(diff, (gi, k0.astype(np.int64)), length)
    np.add.at(diff, (gi, k1.astype(np.int64) + 1), -length)
    km = np.cumsum(diff, axis=1)[:, :n] / 1000.0

    objects = con.execute(
        "SELECT k, count(*) FROM obs GROUP BY k ORDER BY k"
    ).fetchall()
    per_k = dict(objects)

    return {
        "region": region,
        "built": date.today().isoformat(),
        "snapshots": [{"date": d.isoformat(), "key": k} for d, k in zip(dates, keys)],
        "objects": [per_k.get(k, 0) for k in range(n)],
        "groups": [
            {"id": g["id"], "km": [round(float(v), 1) for v in km[i]]}
            for i, g in enumerate(groups)
        ],
        "total_km": [round(float(v), 1) for v in km.sum(axis=0)],
    }
