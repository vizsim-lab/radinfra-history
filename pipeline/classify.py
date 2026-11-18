"""Classify each snapshot with OSMnexus (configs/tilda, bikelanes topic).

One GeoParquet per snapshot, reduced to what the map needs:

    date      INTEGER   snapshot date as YYYYMMDD
    id        VARCHAR   tilda id: way/<id> or way/<id>/<prefix>/<side>
    osm_id    BIGINT
    category  VARCHAR   tilda category id (camelCase, as radinfra.de spells it)
    side      VARCHAR   l / r for a side of a road, s for a way of its own
    name      VARCHAR
    dirs      TINYINT   directions of travel: 1 for a oneway, else 2
    geometry  GEOMETRY  LineString in WGS84, in OSM way direction

Side-split objects keep the road centreline; the map offsets them to their side.

`dirs` is what radinfra.de's Statistik counts a line with (tilda-geo,
tilda_measure_directions): 1 for a oneway (`yes`, `implicit_yes` for a lane on one
side of a road, `yes_dual_carriageway`), else 2. The km here use it only if
versions.COUNT_DIRECTIONS is on.
"""

import tempfile
from pathlib import Path

import duckdb

from . import tools
from .style import CATEGORY_MAP

# OSMnexus' `oneway` values that tilda-geo counts as one direction of travel.
ONEWAY_VALUES = ("yes", "implicit_yes", "yes_dual_carriageway")


def date_key(d):
    return d.year * 10000 + d.month * 100 + d.day


def run_osmnexus(pbf, out_dir, threads):
    tools.run([
        tools.osmnexus(), pbf,
        "--config-dir", tools.osmnexus_config(),
        "--output", "parquet",
        "--out-dir", out_dir,
        # 7 decimals is the PBF's own resolution, so this only strips projection noise.
        "--coordinate-precision", "7",
        "--threads", str(threads),
    ])
    path = Path(out_dir) / "bikelanes.parquet"
    if not path.is_file():
        raise SystemExit(f"OSMnexus wrote no bikelanes.parquet for {pbf}")
    return path


def classify_snapshot(snapshot_date, pbf, out_path, threads=1):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="osmnexus-") as tmp:
        raw = run_osmnexus(pbf, tmp, threads)
        con = duckdb.connect()
        con.execute("CREATE TEMP TABLE category_map (osmnexus VARCHAR, tilda VARCHAR)")
        con.executemany("INSERT INTO category_map VALUES (?, ?)", list(CATEGORY_MAP.items()))

        unknown = con.execute(
            "SELECT DISTINCT category FROM read_parquet(?) "
            "WHERE category NOT IN (SELECT osmnexus FROM category_map)",
            [str(raw)],
        ).fetchall()
        if unknown:
            raise SystemExit(
                f"OSMnexus category without a tilda mapping: {sorted(u[0] for u in unknown)} "
                "— add it to CATEGORY_MAP in pipeline/style.py"
            )

        con.execute(f"""
            COPY (
                SELECT
                    {date_key(snapshot_date)}::INTEGER AS date,
                    r.id,
                    r.osm_id,
                    m.tilda AS category,
                    CASE json_extract_string(r.annotations, '$._side')
                        WHEN 'left' THEN 'l' WHEN 'right' THEN 'r' ELSE 's' END AS side,
                    json_extract_string(r.produced, '$.name') AS name,
                    CASE WHEN json_extract_string(r.produced, '$.oneway') IN {ONEWAY_VALUES}
                         THEN 1 ELSE 2 END::TINYINT AS dirs,
                    r.geometry
                FROM read_parquet('{raw}') r
                JOIN category_map m ON m.osmnexus = r.category
                WHERE r.geometry IS NOT NULL
                ORDER BY r.id
            ) TO '{out_path}' (FORMAT parquet, COMPRESSION zstd)
        """)

        rows, ids = con.execute(
            f"SELECT count(*), count(DISTINCT id) FROM read_parquet('{out_path}')"
        ).fetchone()
        if rows != ids:
            raise SystemExit(f"{out_path}: {rows - ids} duplicate ids — a version would be ambiguous")
    print(f"  {snapshot_date}: {rows:,} objects", flush=True)
    return out_path


def classify_all(snapshots, out_dir, threads=1, force=False):
    """Classify [(date, pbf)]; reuses existing results unless `force`."""
    out_dir = Path(out_dir)
    results = []
    for snapshot_date, pbf in snapshots:
        out_path = out_dir / f"{snapshot_date.isoformat()}.parquet"
        if out_path.is_file() and not force and out_path.stat().st_mtime >= Path(pbf).stat().st_mtime:
            print(f"  {snapshot_date}: reusing {out_path}", flush=True)
        else:
            classify_snapshot(snapshot_date, pbf, out_path, threads)
        results.append((snapshot_date, out_path))
    return results
