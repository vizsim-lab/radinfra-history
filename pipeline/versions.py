"""Turn per-snapshot classifications into versions with a lifespan.

An object (a way, or one side of a road) gets a new version whenever its
category, its geometry, its name or its directions of travel change between two
snapshots, and a version ends when the object disappears. Each version is stored
once, with

    f   first snapshot it appears in        (YYYYMMDD)
    t   first snapshot it no longer appears in, or 99991231 if it still exists

so the state at snapshot date D is exactly the versions with f <= D < t. The map
filters on that, which is why one tileset can serve every point in time and an
unchanged cycleway costs one feature however many snapshots it spans.

A moved node or a new name is a new version but not a new kind of infrastructure,
so each version also carries the span of its category, for the popup:

    cf  first snapshot of the unbroken run of this category on this object
    ct  first snapshot after that run, or 99991231
    pc  category in the snapshot before the run, if the object was drawn there
    nc  category in the snapshot after the run, if the object is drawn there

Alongside, km per legend row and snapshot go to a small JSON for the timeline:
the length of each side of a road and each way of its own (COUNT_DIRECTIONS).
"""

import json
import os
import struct
import tempfile
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import shapely

from .classify import date_key
from .style import drawn_categories, legend_groups

OPEN_END = 99991231
EARTH_RADIUS_M = 6_371_008.8

# radinfra.de's Statistik counts per direction of travel: the length times `dirs`,
# so a way both directions may use counts twice. Over the years that measures
# direction tags more than cycleways. Old data mostly lacks them (2016: 43 of
# 4,919 Berlin roads with a track had a `:oneway`), and radinfra.de then assumes
# both directions, so `cycleway:both=track` counted four times. Off, the km are
# per side of the road and per way, which redrawing a cycleway leaves alone. The
# switch changes only the km, not the versions or the tiles.
COUNT_DIRECTIONS = False


# Versions whose lengths are computed at once: bounds the memory shapely needs.
LENGTH_CHUNK = 500_000


def _connect():
    """DuckDB with a memory cap and a folder to spill to, for Germany's 46 snapshots
    together. RADINFRA_DUCKDB_MEMORY (e.g. 24GiB; default half the RAM) and
    RADINFRA_DUCKDB_TEMP change them."""
    con = duckdb.connect()
    memory = os.environ.get("RADINFRA_DUCKDB_MEMORY")
    if not memory:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        memory = f"{max(total / 2 / 2**30, 1):.0f}GiB"
    temp = os.environ.get("RADINFRA_DUCKDB_TEMP") or str(Path(tempfile.gettempdir()) / "radinfra-duckdb")
    con.execute(f"SET memory_limit = '{memory}'")
    con.execute(f"SET temp_directory = '{temp}'")
    return con


def counted_m(length_m, dirs):
    """What a version adds to the km: its length, per direction if COUNT_DIRECTIONS."""
    return length_m * dirs if COUNT_DIRECTIONS else length_m



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


def build_versions(classified, out_parquet, timeline_json, region=None, coverage=None):
    """classified: [(date, parquet)] oldest first. Writes versions + timeline.
    `coverage`: the bounding box the snapshots cover, kept in the timeline for the
    updates to check their extract against."""
    classified = sorted(classified)
    dates = [d for d, _ in classified]
    if len(set(dates)) != len(dates):
        raise ValueError("duplicate snapshot dates")
    keys = [date_key(d) for d in dates]
    out_parquet = Path(out_parquet)
    out_parquet.parent.mkdir(parents=True, exist_ok=True)

    con = _connect()
    con.execute("CREATE TEMP TABLE snaps (k INTEGER, date INTEGER)")
    con.executemany("INSERT INTO snaps VALUES (?, ?)", list(enumerate(keys)))
    con.execute("CREATE TEMP TABLE drawn (category VARCHAR)")
    con.executemany("INSERT INTO drawn VALUES (?)", [[c] for c in sorted(drawn_categories())])

    files = [str(p) for _, p in classified]
    # Categories the radinfra style does not draw (cyclewayLink) are left out of
    # the tiles and the counts, like on radinfra.de's own map. Every object in
    # every snapshot, but with a hash for its geometry: the geometries themselves
    # are fetched once per version at the end (`shapes`), which keeps this table a
    # fraction of the snapshots' size.
    con.execute("""
        CREATE TEMP TABLE obs AS
        SELECT s.k, c.id, c.osm_id, c.category, c.side, c.name, c.dirs,
               md5_number(c.geometry::BLOB) AS gh
        FROM read_parquet(?) c
        JOIN snaps s USING (date)
        WHERE c.category IN (SELECT category FROM drawn)
    """, [files])

    # Gaps and islands: within one (id, category, geometry, name, dirs),
    # consecutive snapshots share k - row_number(); a gap or a change starts a
    # new island.
    con.execute(f"""
        CREATE TEMP TABLE versions AS
        WITH islands AS (
            SELECT *, k - row_number() OVER (
                PARTITION BY id, category, gh, name, dirs ORDER BY k) AS island
            FROM obs
        ), runs AS (
            SELECT id, category, gh, name, dirs,
                   min(k) AS k0, max(k) AS k1,
                   any_value(osm_id) AS osm_id, any_value(side) AS side
            FROM islands
            GROUP BY id, category, gh, name, dirs, island
        )
        SELECT row_number() OVER (ORDER BY r.id, r.k0)::INTEGER AS vid,
               r.id, r.osm_id, r.category, r.side, r.name, r.dirs,
               sf.date AS f, coalesce(st.date, {OPEN_END}) AS t,
               r.k0, r.k1
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

    # The same gaps and islands over (id, category) alone: how long the object kept
    # its category, whatever happened to its geometry or name meanwhile.
    con.execute(f"""
        CREATE TEMP TABLE spans AS
        WITH islands AS (
            SELECT id, category, k, k - row_number() OVER (
                PARTITION BY id, category ORDER BY k) AS island
            FROM obs
        ), runs AS (
            SELECT id, category, min(k) AS c0, max(k) AS c1
            FROM islands
            GROUP BY id, category, island
        )
        SELECT v.vid, sf.date AS cf, coalesce(st.date, {OPEN_END}) AS ct,
               prev.category AS pc, nxt.category AS nc
        FROM versions v
        JOIN runs r ON r.id = v.id AND r.category = v.category
                   AND r.c0 <= v.k0 AND v.k1 <= r.c1
        JOIN snaps sf ON sf.k = r.c0
        LEFT JOIN snaps st ON st.k = r.c1 + 1
        LEFT JOIN obs prev ON prev.id = v.id AND prev.k = r.c0 - 1
        LEFT JOIN obs nxt ON nxt.id = v.id AND nxt.k = r.c1 + 1
    """)
    n_spans, n_distinct = con.execute("SELECT count(*), count(DISTINCT vid) FROM spans").fetchone()
    n_all = con.execute("SELECT count(*) FROM versions").fetchone()[0]
    if not n_spans == n_distinct == n_all:
        raise SystemExit(f"category spans: {n_spans} rows, {n_distinct} versions, expected {n_all}")

    # Each version's geometry, from its first snapshot (the same throughout: the
    # hash is part of what makes a version).
    con.execute("""
        CREATE TEMP TABLE shapes AS
        SELECT v.vid, c.geometry
        FROM versions v JOIN read_parquet(?) c ON c.id = v.id AND c.date = v.f
    """, [files])
    n_shapes = con.execute("SELECT count(*) FROM shapes").fetchone()[0]
    if n_shapes != n_all:
        raise SystemExit(f"geometries: {n_shapes} for {n_all} versions")

    # Rounded as stored, so the timeline sums what the file holds (and what an
    # appended snapshot later sums again).
    con.execute("CREATE TEMP TABLE lengths (vid INTEGER, length_m DOUBLE)")
    for first in range(1, n_all + 1, LENGTH_CHUNK):
        vid, wkb = con.execute(
            "SELECT vid, geometry::BLOB FROM shapes WHERE vid >= ? AND vid < ? ORDER BY vid",
            [first, first + LENGTH_CHUNK],
        ).fetchnumpy().values()
        con.execute("INSERT INTO lengths SELECT * FROM (SELECT unnest(?) AS vid, unnest(?) AS length_m)",
                    [vid.tolist(), lengths_m(wkb).round(1).tolist()])

    con.execute(f"""
        COPY (
            SELECT v.vid, v.id, v.osm_id, v.category, v.side, v.name, v.dirs, v.f, v.t,
                   s.cf, s.ct, s.pc, s.nc,
                   v.k0, v.k1, l.length_m, g.geometry
            FROM versions v JOIN lengths l USING (vid) JOIN spans s USING (vid) JOIN shapes g USING (vid)
            ORDER BY v.vid
        ) TO '{out_parquet}' (FORMAT parquet, COMPRESSION zstd)
    """)
    vid, category, k0, k1, dirs, length = con.execute(
        "SELECT vid, category, k0, k1, dirs, length_m FROM versions JOIN lengths USING (vid) ORDER BY vid"
    ).fetchnumpy().values()

    timeline = _timeline(dates, keys, category, k0, k1, counted_m(length, dirs), region, coverage)
    _write_json(timeline_json, timeline)

    n_versions = len(vid)
    n_objects = con.execute("SELECT count(DISTINCT id) FROM versions").fetchone()[0]
    print(f"  {n_versions:,} versions of {n_objects:,} objects over {len(dates)} snapshots", flush=True)
    return timeline


# Columns of the versions file, in order; append_snapshot writes the same.
VERSION_COLUMNS = ["vid", "id", "osm_id", "category", "side", "name", "dirs", "f", "t",
                   "cf", "ct", "pc", "nc", "k0", "k1", "length_m", "geometry"]


def append_snapshot(versions_parquet, timeline_json, classified, snapshot_date):
    """Add one snapshot, newer than all before it, to the versions and the timeline.

    The result is what build_versions makes from all snapshots together, except
    for the numbering: versions keep their vid and new ones are numbered on. So
    the quarterly update needs only the versions file, not every snapshot again.
    """
    versions_parquet = Path(versions_parquet)
    timeline = json.loads(Path(timeline_json).read_text(encoding="utf-8"))
    dates = [date.fromisoformat(s["date"]) for s in timeline["snapshots"]]
    if snapshot_date <= dates[-1]:
        raise ValueError(f"{snapshot_date} is not after the last snapshot {dates[-1]}")
    dates.append(snapshot_date)
    keys = [date_key(d) for d in dates]
    kn, dn = len(dates) - 1, keys[-1]

    con = _connect()
    con.execute("CREATE TEMP TABLE drawn (category VARCHAR)")
    con.executemany("INSERT INTO drawn VALUES (?)", [[c] for c in sorted(drawn_categories())])
    con.execute("CREATE TEMP TABLE v AS SELECT * FROM read_parquet(?)", [str(versions_parquet)])
    con.execute("""
        CREATE TEMP TABLE n AS
        SELECT id, osm_id, category, side, name, dirs, md5(geometry::BLOB) AS gh, geometry
        FROM read_parquet(?)
        WHERE category IN (SELECT category FROM drawn)
    """, [str(classified)])
    # The versions alive in the last snapshot; at most one per object.
    con.execute(f"""
        CREATE TEMP TABLE o AS
        SELECT vid, id, category, name, dirs, cf, pc, md5(geometry::BLOB) AS gh
        FROM v WHERE t = {OPEN_END}
    """)

    # Unchanged: the version runs on.
    con.execute("""
        CREATE TEMP TABLE cont AS
        SELECT o.vid, o.id FROM o JOIN n
          ON n.id = o.id AND n.category = o.category AND n.gh = o.gh
         AND n.name IS NOT DISTINCT FROM o.name AND n.dirs = o.dirs
    """)
    con.execute(f"UPDATE v SET k1 = {kn} WHERE vid IN (SELECT vid FROM cont)")
    # Changed or gone: the version ends here.
    con.execute(f"UPDATE v SET t = {dn} WHERE t = {OPEN_END} AND vid NOT IN (SELECT vid FROM cont)")
    # A category run ends where the object is gone or has another category now;
    # every version of the run learns the end and what came after.
    con.execute("""
        CREATE TEMP TABLE ended AS
        SELECT o.id, n.category AS next FROM o LEFT JOIN n USING (id)
        WHERE n.id IS NULL OR n.category <> o.category
    """)
    con.execute(f"UPDATE v SET ct = {dn}, nc = e.next FROM ended e WHERE v.id = e.id AND v.ct = {OPEN_END}")

    # New versions. A changed geometry or name keeps the run of its category.
    con.execute(f"""
        CREATE TEMP TABLE added AS
        SELECT ((SELECT coalesce(max(vid), 0) FROM v) + row_number() OVER (ORDER BY n.id))::INTEGER AS vid,
               n.id, n.osm_id, n.category, n.side, n.name, n.dirs,
               {dn}::INTEGER AS f, {OPEN_END}::INTEGER AS t,
               CASE WHEN o.category = n.category THEN o.cf ELSE {dn} END::INTEGER AS cf,
               {OPEN_END}::INTEGER AS ct,
               CASE WHEN o.category = n.category THEN o.pc ELSE o.category END AS pc,
               NULL::VARCHAR AS nc,
               {kn}::INTEGER AS k0, {kn}::INTEGER AS k1,
               n.geometry
        FROM n LEFT JOIN o USING (id)
        WHERE n.id NOT IN (SELECT id FROM cont)
    """)
    vid, wkb = con.execute("SELECT vid, geometry::BLOB FROM added ORDER BY vid").fetchnumpy().values()
    con.execute("CREATE TEMP TABLE added_lengths (vid INTEGER, length_m DOUBLE)")
    if len(vid):
        con.execute("INSERT INTO added_lengths SELECT * FROM (SELECT unnest(?) AS vid, unnest(?) AS length_m)",
                    [vid.tolist(), lengths_m(wkb).round(1).tolist()])

    columns = ", ".join(VERSION_COLUMNS)
    tmp = versions_parquet.with_suffix(".tmp.parquet")
    con.execute(f"""
        COPY (
            SELECT {columns} FROM v
            UNION ALL
            SELECT {columns} FROM added JOIN added_lengths USING (vid)
            ORDER BY vid
        ) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)
    """)
    category, k0, k1, length, dirs = con.execute(
        f"SELECT category, k0, k1, length_m, dirs FROM read_parquet('{tmp}') ORDER BY vid"
    ).fetchnumpy().values()
    tmp.replace(versions_parquet)

    timeline = _timeline(dates, keys, category, k0, k1, counted_m(length, dirs), timeline.get("region"),
                         timeline.get("coverage"))
    _write_json(timeline_json, timeline)
    # Why versions end, for the log of the unattended update: a jump in one reason
    # (say, geometry from a differently cut extract) is easy to spot there.
    reasons = con.execute("""
        SELECT count(*) FILTER (WHERE n.id IS NULL),
               count(*) FILTER (WHERE n.category <> o.category),
               count(*) FILTER (WHERE n.category = o.category AND n.gh <> o.gh),
               count(*) FILTER (WHERE n.category = o.category AND n.gh = o.gh
                                AND n.name IS DISTINCT FROM o.name),
               count(*) FILTER (WHERE n.category = o.category AND n.gh = o.gh
                                AND n.name IS NOT DISTINCT FROM o.name AND n.dirs <> o.dirs)
        FROM o LEFT JOIN n USING (id)
        WHERE o.vid NOT IN (SELECT vid FROM cont)
    """).fetchone()
    continued = con.execute("SELECT count(*) FROM cont").fetchone()[0]
    print(f"  {snapshot_date}: {continued:,} versions run on, {sum(reasons):,} end "
          f"(gone {reasons[0]:,}, category {reasons[1]:,}, geometry {reasons[2]:,}, "
          f"name {reasons[3]:,}, directions {reasons[4]:,}), {len(vid):,} new", flush=True)
    return timeline


# The popup's dates, kept out of the tiles: one record per version id, so the page
# reads the eight bytes of a clicked line with one range request.
#   cf, ct  snapshot indices (uint16; ct 0xFFFF while the run is open)
#   pc, nc  indices into the timeline's `categories` (uint8; 0xFF for none)
#   pm, nm  what was at the place before the run and is after it, by
#           pipeline/changes.py: the index of the category it replaced or that
#           replaced it, plus ON_ROAD where that was a side of a road; PLACE_BESIDE
#           where something else was or stays there, PLACE_EMPTY where nothing;
#           0xFF where not known (first snapshot, run still open, no changes file)
SPAN_RECORD = struct.Struct("<HHBBBB")
NO_SNAPSHOT = 0xFFFF
NO_CATEGORY = 0xFF
ON_ROAD = 0x80
PLACE_BESIDE = 0xFD
PLACE_EMPTY = 0xFE


# The same record as a numpy type, to write millions of them at once.
SPAN_DTYPE = np.dtype([("cf", "<u2"), ("ct", "<u2"), ("pc", "u1"), ("nc", "u1"), ("pm", "u1"), ("nm", "u1")])
assert SPAN_DTYPE.itemsize == SPAN_RECORD.size


def _place_code(rep, other, empty, category, on_road):
    """SQL for pm or nm: the largest of the three shares decides."""
    return f"""CASE WHEN {rep} IS NULL THEN {NO_CATEGORY}
                    WHEN {rep} >= {other} AND {rep} >= {empty} AND {category} IS NOT NULL
                         THEN {category} + CASE WHEN {on_road} THEN {ON_ROAD} ELSE 0 END
                    WHEN {other} >= {empty} THEN {PLACE_BESIDE}
                    ELSE {PLACE_EMPTY} END"""


def write_spans(versions_parquet, timeline_json, out_path, changes_parquet=None):
    """The popup file. `changes_parquet` (pipeline/changes.py) gives pm and nm."""
    timeline = json.loads(Path(timeline_json).read_text(encoding="utf-8"))
    keys = [s["key"] for s in timeline["snapshots"]]
    categories = timeline["categories"]
    if len(keys) >= NO_SNAPSHOT or len(categories) >= ON_ROAD:
        raise SystemExit("too many snapshots or categories for the popup file's records")
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE snap_index AS SELECT unnest(?) AS key, generate_subscripts(?, 1) - 1 AS i",
                [keys, keys])
    con.execute("CREATE TEMP TABLE codes AS SELECT unnest(?) AS category, generate_subscripts(?, 1) - 1 AS i",
                [categories, categories])
    # A run's place before is that of its first version, its place after that of its last.
    if changes_parquet and Path(changes_parquet).is_file():
        con.execute(f"""
            CREATE TEMP TABLE places AS
            WITH v AS (SELECT vid, id, f, t, cf, ct FROM read_parquet('{versions_parquet}')),
                 c AS (SELECT * FROM read_parquet('{changes_parquet}')),
                 first AS (SELECT v.id, v.cf, c.* FROM v JOIN c USING (vid) WHERE v.f = v.cf),
                 last AS (SELECT v.id, v.ct, c.* FROM v JOIN c USING (vid) WHERE v.t = v.ct AND v.t <> {OPEN_END})
            SELECT v.vid,
                   {_place_code("a.s_rep", "a.s_add", "a.s_new", "pa.i", "a.s_road")} AS pm,
                   {_place_code("b.e_rep", "b.e_red", "b.e_gone", "pb.i", "b.e_road")} AS nm
            FROM v
            LEFT JOIN first a ON a.id = v.id AND a.cf = v.cf
            LEFT JOIN last b ON b.id = v.id AND b.ct = v.ct
            LEFT JOIN codes pa ON pa.category = a.s_from
            LEFT JOIN codes pb ON pb.category = b.e_to
        """)
    else:
        con.execute(f"""CREATE TEMP TABLE places AS SELECT vid, {NO_CATEGORY} AS pm, {NO_CATEGORY} AS nm
                        FROM read_parquet('{versions_parquet}')""")
    vid, cf, ct, pc, nc, pm, nm, unknown = con.execute(f"""
        SELECT v.vid, sf.i, coalesce(st.i, {NO_SNAPSHOT}), coalesce(cp.i, {NO_CATEGORY}),
               coalesce(cn.i, {NO_CATEGORY}), coalesce(p.pm, {NO_CATEGORY}), coalesce(p.nm, {NO_CATEGORY}),
               (sf.i IS NULL OR (st.i IS NULL AND v.ct <> {OPEN_END})
                OR (cp.i IS NULL AND v.pc IS NOT NULL) OR (cn.i IS NULL AND v.nc IS NOT NULL))
        FROM read_parquet('{versions_parquet}') v
        LEFT JOIN snap_index sf ON sf.key = v.cf
        LEFT JOIN snap_index st ON st.key = v.ct
        LEFT JOIN codes cp ON cp.category = v.pc
        LEFT JOIN codes cn ON cn.category = v.nc
        LEFT JOIN places p ON p.vid = v.vid
        ORDER BY v.vid
    """).fetchnumpy().values()
    if len(vid) and np.asarray(unknown).any():
        raise SystemExit(f"{versions_parquet}: spans with a snapshot or category the timeline does not have")
    records = np.zeros(int(vid[-1]) + 1 if len(vid) else 0, dtype=SPAN_DTYPE)
    for name, values in (("cf", cf), ("ct", ct), ("pc", pc), ("nc", nc), ("pm", pm), ("nm", nm)):
        records[name][vid] = values
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(records.tobytes())
    print(f"  {out_path}: {records.nbytes / 1e6:.1f} MB", flush=True)
    return out_path


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _timeline(dates, keys, category, k0, k1, length, region, coverage=None):
    """km per legend row and snapshot, by a difference array over each lifespan.
    `length` is what each version counts, in metres (counted_m)."""
    n = len(dates)
    groups = legend_groups()
    group_of = {c: i for i, g in enumerate(groups) for c in g["categories"]}
    gi = np.array([group_of[c] for c in category], dtype=np.int64)
    k0 = np.asarray(k0, dtype=np.int64)
    k1 = np.asarray(k1, dtype=np.int64)
    length = np.asarray(length, dtype=float)
    # Summed in a fixed order, so the same versions give the same km to the last
    # digit whichever way they were numbered (full build or appended snapshots).
    order = np.lexsort((length, k1, k0, gi))
    gi, k0, k1, length = gi[order], k0[order], k1[order], length[order]

    diff = np.zeros((len(groups), n + 1))
    np.add.at(diff, (gi, k0), length)
    np.add.at(diff, (gi, k1 + 1), -length)
    km = np.cumsum(diff, axis=1)[:, :n] / 1000.0
    # + 0.0 below: a category that empties sums to -0.0, which the page would print.

    # Objects per snapshot: every object has exactly one version alive there.
    alive = np.zeros(n + 1)
    np.add.at(alive, k0, 1)
    np.add.at(alive, k1 + 1, -1)
    objects = np.cumsum(alive)[:n]

    extra = {"coverage": [float(v) for v in coverage]} if coverage else {}
    return {
        "region": region,
        **extra,
        "built": date.today().isoformat(),
        "unit": "km je Fahrtrichtung" if COUNT_DIRECTIONS else "km je Straßenseite und Weg",
        # Category codes of the popup file (write_spans), in this order, and the size
        # of its records (six bytes in builds before November 2026).
        "categories": sorted(drawn_categories()),
        "span_size": SPAN_RECORD.size,
        "snapshots": [{"date": d.isoformat(), "key": k} for d, k in zip(dates, keys)],
        "objects": [int(v) for v in objects],
        "groups": [
            {"id": g["id"], "km": [round(float(v), 1) + 0.0 for v in km[i]]}
            for i, g in enumerate(groups)
        ],
        "total_km": [round(float(v), 1) + 0.0 for v in km.sum(axis=0)],
    }
