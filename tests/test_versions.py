"""Versions and the timeline, on hand-made classified snapshots."""

import json
import shutil
import subprocess
from datetime import date

import duckdb
import pytest

from pipeline import snapshots, versions
from pipeline.classify import date_key
from pipeline.style import CATEGORY_MAP, drawn_categories, legend_groups
from pipeline.versions import (
    NO_CATEGORY, NO_SNAPSHOT, OPEN_END, SPAN_RECORD,
    append_snapshot, build_versions, lengths_m, write_spans,
)

DATES = [date(2020, 1, 1), date(2020, 4, 1), date(2020, 7, 1), date(2020, 10, 1)]
LINE_A = "LINESTRING(13.40 52.50, 13.41 52.50)"
LINE_B = "LINESTRING(13.40 52.51, 13.41 52.51)"


def write_snapshot(path, d, rows):
    """rows: (id, category, side, name, wkt, dirs)"""
    con = duckdb.connect()
    con.execute("CREATE TABLE s (id VARCHAR, category VARCHAR, side VARCHAR, name VARCHAR, "
                "wkt VARCHAR, dirs TINYINT)")
    if rows:
        con.executemany("INSERT INTO s VALUES (?, ?, ?, ?, ?, ?)", rows)
    con.execute(f"""
        COPY (
            SELECT {date_key(d)}::INTEGER AS date, id,
                   CAST(regexp_extract(id, 'way/(\\d+)', 1) AS BIGINT) AS osm_id,
                   category, side, name, dirs, wkt::GEOMETRY AS geometry
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
        # oneway from the third snapshot (oneway=yes added): a new version, the same run
        "way/6": [("footAndCyclewayShared_isolated", LINE_B, 2)] * 2
                 + [("footAndCyclewayShared_isolated", LINE_B, 1)] * 2,
    }
    classified = []
    for k, d in enumerate(DATES):
        rows = []
        for obj_id, seq in states.items():
            if seq[k] is None:
                continue
            category, wkt, *dirs = seq[k]
            side = "r" if obj_id.endswith("/right") else "s"
            # A lane on one side of the road runs one way; other ways run both.
            dirs = dirs[0] if dirs else (1 if side == "r" else 2)
            rows.append((obj_id, category, side, "Teststraße", wkt, dirs))
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
        ("way/6", "footAndCyclewayShared_isolated", k[0], k[2]),
        ("way/6", "footAndCyclewayShared_isolated", k[2], OPEN_END),
    ]


CONTENT = "id, osm_id, category, side, name, dirs, f, t, cf, ct, pc, nc, k0, k1, length_m, md5(geometry::BLOB)"


def content_of(path):
    return duckdb.connect().execute(
        f"SELECT {CONTENT} FROM read_parquet('{path}') ORDER BY id, k0"
    ).fetchall()


@pytest.mark.parametrize("per_direction", [False, True])
@pytest.mark.parametrize("start", [1, 2, 3])
def test_append_matches_full_build(history, tmp_path, monkeypatch, start, per_direction):
    """Snapshots added one by one give what a build from all of them gives."""
    monkeypatch.setattr(versions, "COUNT_DIRECTIONS", per_direction)
    full, full_timeline = tmp_path / "full.parquet", tmp_path / "full.json"
    build_versions(history, full, full_timeline, region="Test")
    inc, inc_timeline = tmp_path / "inc.parquet", tmp_path / "inc.json"
    build_versions(history[:start], inc, inc_timeline, region="Test")
    before = duckdb.connect().execute(f"SELECT vid, id, k0 FROM read_parquet('{inc}')").fetchall()

    for d, path in history[start:]:
        append_snapshot(inc, inc_timeline, path, d)

    assert content_of(inc) == content_of(full)
    # The build id names the file, which is numbered differently; the rest is the same.
    inc_json, full_json = json.loads(inc_timeline.read_text()), json.loads(full_timeline.read_text())
    assert inc_json.pop("build") != full_json.pop("build")
    assert inc_json == full_json
    # Versions keep their numbers; new ones are numbered on.
    after = set(duckdb.connect().execute(f"SELECT vid, id, k0 FROM read_parquet('{inc}')").fetchall())
    assert set(before) <= after
    vids = [v for v, _, _ in after]
    assert len(vids) == len(set(vids))


def test_append_refuses_an_older_snapshot(history, tmp_path):
    out, timeline = tmp_path / "v.parquet", tmp_path / "t.json"
    build_versions(history, out, timeline)
    with pytest.raises(ValueError):
        append_snapshot(out, timeline, history[-1][1], DATES[-1])


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
        # so does a change of the directions of travel
        ("way/6", k[0], k[0], OPEN_END, None, None),
        ("way/6", k[2], k[0], OPEN_END, None, None),
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
    a, b = km_of(LINE_A, 1), km_of(LINE_B, 1)
    assert a == pytest.approx(0.7, abs=0.05)  # 0.01° of longitude at 52.5° N
    # km per side of the road and per way: the directions of travel do not count.
    assert timeline["unit"] == "km je Straßenseite und Weg"
    assert km["Getrennter Radweg"] == [a] * 4
    assert km["Fahrradstrasse keine Kfz"] == [a, 0, 0, a]
    assert km["Markierung Kreuzungsbereich"] == [a, b, b, 0]
    assert km["Schutzstreifen"] == [b, b, 0, 0]
    assert km["Radfahrstreifen"] == [0, 0, b, b]
    assert km["Gemeinsamer Geh u Radweg"] == [b] * 4
    assert timeline["objects"] == [5, 4, 4, 4]  # cyclewayLink is not counted


def test_timeline_per_direction(history, tmp_path, monkeypatch):
    """With COUNT_DIRECTIONS, as radinfra.de's Statistik: a way both directions may
    use counts twice, a lane on one side of the road once."""
    monkeypatch.setattr(versions, "COUNT_DIRECTIONS", True)
    timeline_path = tmp_path / "timeline.json"
    build_versions(history, tmp_path / "versions.parquet", timeline_path)
    timeline = json.loads(timeline_path.read_text())
    km = {g["id"]: g["km"] for g in timeline["groups"]}
    a1, a2 = km_of(LINE_A, 1), km_of(LINE_A, 2)
    b1, b2 = km_of(LINE_B, 1), km_of(LINE_B, 2)
    assert a2 == pytest.approx(2 * a1, abs=0.1)
    assert timeline["unit"] == "km je Fahrtrichtung"
    assert km["Getrennter Radweg"] == [a2] * 4
    assert km["Fahrradstrasse keine Kfz"] == [a2, 0, 0, a2]
    assert km["Markierung Kreuzungsbereich"] == [a2, b2, b2, 0]
    assert km["Schutzstreifen"] == [b1, b1, 0, 0]
    assert km["Radfahrstreifen"] == [0, 0, b1, b1]
    assert km["Gemeinsamer Geh u Radweg"] == [b2, b2, b1, b1]


def km_of(wkt, dirs):
    """What one version of this line counts in the timeline, in km."""
    wkb = duckdb.connect().execute(f"SELECT '{wkt}'::GEOMETRY::BLOB").fetchone()[0]
    return round(round(float(lengths_m([wkb])[0]), 1) * dirs / 1000, 1)


def test_spans_file(history, tmp_path):
    """The popup file holds every version's category span at offset vid * 6."""
    out, timeline_path, spans = tmp_path / "v.parquet", tmp_path / "t.json", tmp_path / "spans.bin"
    build_versions(history, out, timeline_path)
    write_spans(out, timeline_path, spans)
    timeline = json.loads(timeline_path.read_text())
    keys = [s["key"] for s in timeline["snapshots"]]
    categories = timeline["categories"]
    rows = duckdb.connect().execute(
        f"SELECT vid, cf, ct, pc, nc FROM read_parquet('{out}') ORDER BY vid"
    ).fetchall()
    data = spans.read_bytes()
    assert len(data) == (rows[-1][0] + 1) * SPAN_RECORD.size
    assert any(pc for *_, pc, _ in rows) and any(nc for *_, nc in rows)
    for vid, cf, ct, pc, nc in rows:
        i_cf, i_ct, i_pc, i_nc = SPAN_RECORD.unpack_from(data, vid * SPAN_RECORD.size)
        assert keys[i_cf] == cf
        assert (OPEN_END if i_ct == NO_SNAPSHOT else keys[i_ct]) == ct
        assert (None if i_pc == NO_CATEGORY else categories[i_pc]) == pc
        assert (None if i_nc == NO_CATEGORY else categories[i_nc]) == nc


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


@pytest.mark.skipif(shutil.which("osmium") is None, reason="needs osmium-tool")
def test_edited_since(tmp_path):
    opl = tmp_path / "t.opl"
    opl.write_text(
        "n1 v1 dV c0 t2026-01-01T00:00:00Z i0 u T x13.4 y52.5\n"
        "n2 v1 dV c0 t2026-01-01T00:00:00Z i0 u T x13.41 y52.5\n"
        "w1 v2 dV c0 t2026-11-12T10:00:00Z i0 u Thighway=cycleway Nn1,n2\n"
        "w2 v1 dV c0 t2026-10-01T08:00:00Z i0 u Thighway=cycleway Nn1,n2\n"
    )
    pbf = tmp_path / "t.osm.pbf"
    subprocess.run(["osmium", "cat", opl, "-o", pbf], check=True)
    since = date(2026, 11, 9)
    assert snapshots.edited_since(pbf, [1, 2, 3], since) == 1  # way 3 is not in the file
    assert snapshots.edited_since(pbf, [], since) == 0
    # Snapshots cut from a history file carry no timestamps.
    bare = tmp_path / "bare.osm.pbf"
    subprocess.run(["osmium", "cat", pbf, "-o", bare, "-f", "pbf,add_metadata=false"], check=True)
    assert snapshots.edited_since(bare, [1, 2], since) is None
