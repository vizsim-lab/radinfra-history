"""Versions and the timeline, on hand-made classified snapshots."""

import json
from datetime import date

import duckdb
import pytest

from pipeline import snapshots
from pipeline.classify import date_key
from pipeline.style import CATEGORY_MAP, drawn_categories, legend_groups
from pipeline.versions import OPEN_END, build_versions, lengths_m

DATES = [date(2020, 1, 1), date(2020, 4, 1), date(2020, 7, 1), date(2020, 10, 1)]
LINE_A = "LINESTRING(13.40 52.50, 13.41 52.50)"
LINE_B = "LINESTRING(13.40 52.51, 13.41 52.51)"


def write_snapshot(path, d, rows):
    """rows: (id, category, side, name, wkt)"""
    con = duckdb.connect()
    con.execute("CREATE TABLE s (id VARCHAR, category VARCHAR, side VARCHAR, name VARCHAR, wkt VARCHAR)")
    if rows:
        con.executemany("INSERT INTO s VALUES (?, ?, ?, ?, ?)", rows)
    con.execute(f"""
        COPY (
            SELECT {date_key(d)}::INTEGER AS date, id,
                   CAST(regexp_extract(id, 'way/(\\d+)', 1) AS BIGINT) AS osm_id,
                   category, side, name, wkt::GEOMETRY AS geometry
            FROM s
        ) TO '{path}' (FORMAT parquet)
    """)


@pytest.fixture
def history(tmp_path):
    states = {
        # unchanged throughout
        "way/1": [("cycleway_adjoining", LINE_A)] * 4,
        # category change at the third snapshot
        "way/2/cycleway/right": [("cyclewayOnHighway_advisory", LINE_B)] * 2
                                + [("cyclewayOnHighway_exclusive", LINE_B)] * 2,
        # gone in the second snapshot, back in the fourth
        "way/3": [("bicycleRoad", LINE_A), None, None, ("bicycleRoad", LINE_A)],
        # geometry change at the second snapshot, removed at the last
        "way/4": [("crossing", LINE_A), ("crossing", LINE_B), ("crossing", LINE_B), None],
        # a category the style does not draw
        "way/5": [("cyclewayLink", LINE_A)] * 4,
    }
    classified = []
    for k, d in enumerate(DATES):
        rows = []
        for obj_id, seq in states.items():
            if seq[k] is None:
                continue
            category, wkt = seq[k]
            side = "r" if obj_id.endswith("/right") else "s"
            rows.append((obj_id, category, side, "Teststraße", wkt))
        path = tmp_path / f"{d.isoformat()}.parquet"
        write_snapshot(path, d, rows)
        classified.append((d, path))
    return classified


def versions_of(path):
    con = duckdb.connect()
    return con.execute(
        f"SELECT id, category, f, t FROM read_parquet('{path}') ORDER BY id, f"
    ).fetchall()


def test_versions(history, tmp_path):
    out = tmp_path / "versions.parquet"
    build_versions(history, out, tmp_path / "timeline.json", region="Test")
    k = [date_key(d) for d in DATES]
    assert versions_of(out) == [
        ("way/1", "cycleway_adjoining", k[0], OPEN_END),
        ("way/2/cycleway/right", "cyclewayOnHighway_advisory", k[0], k[2]),
        ("way/2/cycleway/right", "cyclewayOnHighway_exclusive", k[2], OPEN_END),
        ("way/3", "bicycleRoad", k[0], k[1]),
        ("way/3", "bicycleRoad", k[3], OPEN_END),
        ("way/4", "crossing", k[0], k[1]),
        ("way/4", "crossing", k[1], k[3]),
    ]


def test_category_spans(history, tmp_path):
    """cf/ct date the category, not the version: geometry changes do not count."""
    out = tmp_path / "versions.parquet"
    build_versions(history, out, tmp_path / "timeline.json")
    k = [date_key(d) for d in DATES]
    rows = duckdb.connect().execute(
        f"SELECT id, f, cf, ct, pc, nc FROM read_parquet('{out}') ORDER BY id, f"
    ).fetchall()
    assert rows == [
        ("way/1", k[0], k[0], OPEN_END, None, None),
        ("way/2/cycleway/right", k[0], k[0], k[2], None, "cyclewayOnHighway_exclusive"),
        ("way/2/cycleway/right", k[2], k[2], OPEN_END, "cyclewayOnHighway_advisory", None),
        # gone and back: two runs, nothing drawn in between
        ("way/3", k[0], k[0], k[1], None, None),
        ("way/3", k[3], k[3], OPEN_END, None, None),
        # the geometry change at k[1] starts a version but not a run
        ("way/4", k[0], k[0], k[3], None, None),
        ("way/4", k[1], k[0], k[3], None, None),
    ]


def test_state_at_each_date_matches_snapshot(history, tmp_path):
    """f <= D < t must reproduce every snapshot exactly (drawn categories only)."""
    out = tmp_path / "versions.parquet"
    build_versions(history, out, tmp_path / "timeline.json")
    con = duckdb.connect()
    drawn = sorted(drawn_categories())
    for d, path in history:
        key = date_key(d)
        expected = con.execute(
            f"SELECT id, category, md5(geometry::BLOB) FROM read_parquet('{path}') "
            "WHERE list_contains(?, category) ORDER BY id", [drawn]
        ).fetchall()
        got = con.execute(
            f"SELECT id, category, md5(geometry::BLOB) FROM read_parquet('{out}') "
            f"WHERE f <= {key} AND t > {key} ORDER BY id"
        ).fetchall()
        assert got == expected, d


def test_timeline(history, tmp_path):
    timeline_path = tmp_path / "timeline.json"
    build_versions(history, tmp_path / "versions.parquet", timeline_path)
    timeline = json.loads(timeline_path.read_text())
    assert [s["date"] for s in timeline["snapshots"]] == [d.isoformat() for d in DATES]
    km = {g["id"]: g["km"] for g in timeline["groups"]}
    wkb = duckdb.connect().execute(f"SELECT '{LINE_A}'::GEOMETRY::BLOB").fetchone()[0]
    line_km = round(float(lengths_m([wkb])[0]) / 1000, 1)
    assert line_km == pytest.approx(0.7, abs=0.05)  # 0.01° of longitude at 52.5° N
    assert km["Getrennter Radweg"] == [line_km] * 4
    assert km["Fahrradstrasse keine Kfz"] == [line_km, 0, 0, line_km]
    assert km["Markierung Kreuzungsbereich"] == [line_km, line_km, line_km, 0]
    assert km["Schutzstreifen"] == [line_km, line_km, 0, 0]
    assert km["Radfahrstreifen"] == [0, 0, line_km, line_km]
    assert timeline["objects"] == [4, 3, 3, 3]  # cyclewayLink is not counted


def test_style_covers_every_mapped_category():
    drawn = drawn_categories()
    mapped = set(CATEGORY_MAP.values())
    # Everything the style draws must be producible from OSMnexus output …
    assert drawn <= mapped
    # … and the only mapped category it does not draw is cyclewayLink.
    assert mapped - drawn == {"cyclewayLink"}
    assert len(legend_groups()) == 13


def test_regular_dates():
    assert snapshots.regular_dates(date(2020, 1, 1), date(2020, 12, 31), 3) == [
        date(2020, 1, 1), date(2020, 4, 1), date(2020, 7, 1), date(2020, 10, 1)]
    assert snapshots.regular_dates(date(2020, 1, 15), date(2020, 6, 1), 2) == [
        date(2020, 2, 1), date(2020, 4, 1), date(2020, 6, 1)]


def test_date_from_filename():
    assert snapshots.date_from_filename("data/2021-07-01.osm.pbf") == date(2021, 7, 1)
    assert snapshots.date_from_filename("berlin-210101.osm.pbf") == date(2021, 1, 1)
    with pytest.raises(ValueError):
        snapshots.date_from_filename("berlin-latest.osm.pbf")
