"""Where the km change (pipeline/changes.py), on hand-made snapshots: one case per row
of streets, each far from the others."""

from datetime import date

import duckdb
import numpy as np
import pytest
import shapely

import json

from pipeline import areas, changes
from pipeline.classify import date_key
from pipeline.regions import Region
from pipeline.style import legend_groups
from pipeline.versions import (
    EARTH_RADIUS_M, NO_CATEGORY, ON_ROAD, OPEN_END, PLACE_BESIDE, PLACE_EMPTY, SPAN_RECORD,
    build_versions, write_spans,
)

DATES = [date(2020, 1, 1), date(2020, 4, 1), date(2020, 7, 1), date(2020, 10, 1)]
# As pipeline/versions.py measures: metres per degree on the sphere.
M_PER_DEG = EARTH_RADIUS_M * np.pi / 180


def line(row, north_m=0.0, x0=0.0, x1=200.0):
    """An east-west line of row `row`, `north_m` north of the row's axis, from x0 to
    x1 metres east (west if x1 < x0)."""
    lat = 52.5 + 0.01 * row + north_m / M_PER_DEG
    lon = lambda x: 13.4 + x / (M_PER_DEG * np.cos(np.radians(lat)))  # noqa: E731
    return f"LINESTRING({lon(x0):.9f} {lat:.9f}, {lon(x1):.9f} {lat:.9f})"


# Object id -> its state at each date: (category, wkt[, name]) or None.
LANE, ADVISORY = "cyclewayOnHighway_exclusive", "cyclewayOnHighway_advisory"
TRACK, PATH = "cycleway_adjoining", "cycleway_isolated"
SHARED, ROAD = "footAndCyclewayShared_isolated", "bicycleRoad"
STATES = {
    # retagged at the third snapshot: advisory lane -> lane, the same line
    "way/10/cycleway/right": [(ADVISORY, line(1))] * 2 + [(LANE, line(1))] * 2,
    # a track on the road's tags becomes a way of its own 8 m to the right, at once
    "way/20/cycleway/right": [(TRACK, line(2))] * 2 + [None] * 2,
    "way/21": [None] * 2 + [(TRACK, line(2, -8))] * 2,
    # split into two ways at the second snapshot
    "way/30": [(SHARED, line(3))] + [(SHARED, line(3, 0, 0, 120))] * 3,
    "way/31": [None] + [(SHARED, line(3, 0, 120, 200))] * 3,
    # new where nothing was
    "way/40": [None] + [(ROAD, line(4))] * 3,
    # gone, nothing left
    "way/50": [(ROAD, line(5))] * 2 + [None] * 2,
    # a lane that stays, and a cycleway of its own next to it from the third snapshot
    "way/60/cycleway/right": [(LANE, line(6))] * 4,
    "way/61": [None] * 2 + [(PATH, line(6, -6))] * 2,
    # a lane on the right throughout, one on the left from the second snapshot
    "way/70/cycleway/right": [(LANE, line(7))] * 4,
    "way/70/cycleway/left": [None] + [(LANE, line(7))] * 3,
    # the way of its own comes a quarter before the road's tag goes
    "way/80/cycleway/right": [(TRACK, line(8))] * 2 + [None] * 2,
    "way/81": [None] + [(TRACK, line(8, -8))] * 3,
    # the road is reversed: right becomes left, on the same side of the street
    "way/90/cycleway/right": [(ADVISORY, line(9))] + [None] * 3,
    "way/90/cycleway/left": [None] + [(ADVISORY, line(9, 0, 200, 0))] * 3,
    # a path renamed at the third snapshot (an edit, not a replacement), and a new
    # path 10 m beside it from the second
    "way/100": [(PATH, line(10))] * 2 + [(PATH, line(10), "Neuer Name")] * 2,
    "way/101": [None] + [(PATH, line(10, 10))] * 3,
    # a lane retagged a quarter after a path of its own came next to it: the lane stays
    "way/110/cycleway/right": [(LANE, line(11))] * 2 + [(ADVISORY, line(11))] * 2,
    "way/111": [None] + [(PATH, line(11, -8))] * 3,
}


def write_snapshot(path, d, rows):
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
def build(tmp_path):
    classified = []
    for k, d in enumerate(DATES):
        rows = []
        for obj_id, seq in STATES.items():
            if seq[k] is None:
                continue
            category, wkt, *name = seq[k]
            side = {"right": "r", "left": "l"}.get(obj_id.rsplit("/", 1)[-1], "s")
            rows.append((obj_id, category, side, name[0] if name else "Teststraße", wkt, 1 if side != "s" else 2))
        path = tmp_path / f"{d.isoformat()}.parquet"
        write_snapshot(path, d, rows)
        classified.append((d, path))
    build_versions(classified, tmp_path / "versions.parquet", tmp_path / "timeline.json", region="Test")
    return tmp_path


def rows_of(build, window=changes.WINDOW, rows=None):
    """Per step: {(start|end, kind, group): km} for the lines of these rows of streets."""
    bbox = None
    if rows is not None:
        lat = [52.5 + 0.01 * r for r in rows]
        bbox = (13.39, min(lat) - 0.004, 13.41, max(lat) + 0.004)
    result = changes.analyse(build / "versions.parquet", build / "timeline.json", bbox=bbox, window=window)
    out = []
    for step in result["steps"]:
        found = {}
        for which, kinds in (("start", changes.START_KINDS), ("end", changes.END_KINDS)):
            for i, kind in enumerate(kinds):
                for g, value in enumerate(step[which][i]):
                    if value > 1e-6:
                        found[(which, kind, result["groups"][g])] = round(value * 1000)
        out.append(found)
    return out, result


def test_retagging_is_replaced_in_place(build):
    steps, result = rows_of(build, rows=[1])
    assert steps[1] == {("start", "replaced", "Radfahrstreifen"): 200, ("end", "replaced", "Schutzstreifen"): 200}
    flows = result["steps"][1]["flow_in"]
    groups = result["groups"]
    assert flows[groups.index("Radfahrstreifen"), groups.index("Schutzstreifen")] == pytest.approx(0.2, abs=1e-3)


def test_a_track_moved_to_a_way_of_its_own_is_replaced(build):
    steps, _ = rows_of(build, rows=[2])
    assert steps[1] == {("start", "replaced", "Getrennter Radweg"): 200, ("end", "replaced", "Getrennter Radweg"): 200}


def test_a_split_way_is_replaced(build):
    steps, _ = rows_of(build, rows=[3])
    assert steps[0] == {("start", "replaced", "Gemeinsamer Geh u Radweg"): 200,
                        ("end", "replaced", "Gemeinsamer Geh u Radweg"): 200}


def test_new_and_gone_places(build):
    steps, _ = rows_of(build, rows=[4])
    assert steps[0] == {("start", "new", "Fahrradstrasse keine Kfz"): 200}
    steps, _ = rows_of(build, rows=[5])
    assert steps[1] == {("end", "gone", "Fahrradstrasse keine Kfz"): 200}


def test_a_way_next_to_a_lane_that_stays_is_added(build):
    steps, _ = rows_of(build, rows=[6])
    assert steps[1] == {("start", "added", "Getrennter Radweg"): 200}


def test_a_second_side_is_new_not_the_first_one_again(build):
    steps, _ = rows_of(build, rows=[7])
    assert steps[0] == {("start", "new", "Radfahrstreifen"): 200}


def test_a_way_of_its_own_a_quarter_early_is_replaced(build):
    steps, _ = rows_of(build, rows=[8])
    assert steps[0] == {("start", "replaced", "Getrennter Radweg"): 200}
    assert steps[1] == {("end", "replaced", "Getrennter Radweg"): 200}
    # Without the window: a second line at a place that had one, then one less.
    steps, _ = rows_of(build, window=0, rows=[8])
    assert steps[0] == {("start", "added", "Getrennter Radweg"): 200}
    assert steps[1] == {("end", "reduced", "Getrennter Radweg"): 200}


def test_a_reversed_road_keeps_its_side(build):
    steps, _ = rows_of(build, rows=[9])
    assert steps[0] == {("start", "replaced", "Schutzstreifen"): 200, ("end", "replaced", "Schutzstreifen"): 200}


def test_an_edit_next_door_is_no_replacement(build):
    steps, _ = rows_of(build, rows=[10])
    # The new path is beside one that stays (its rename a quarter later is an edit).
    assert steps[0] == {("start", "added", "Getrennter Radweg"): 200}
    assert steps[1] == {("start", "replaced", "Getrennter Radweg"): 200, ("end", "replaced", "Getrennter Radweg"): 200}


def test_the_parts_add_up_to_what_starts_and_ends(build):
    """Δ = what starts − what ends, per legend row and step, which is what the
    timeline sums (it rounds to 100 m)."""
    _, result = rows_of(build)
    timeline = json.loads((build / "timeline.json").read_text())
    groups = {c: g["id"] for g in legend_groups() for c in g["categories"]}
    rows = duckdb.connect().execute(
        f"SELECT category, k0, k1, t, length_m FROM read_parquet('{build / 'versions.parquet'}')").fetchall()
    for k, step in enumerate(result["steps"], start=1):
        for g, group in enumerate(result["groups"]):
            starts = sum(m for c, k0, _, _, m in rows if groups[c] == group and k0 == k)
            ends = sum(m for c, _, k1, t, m in rows if groups[c] == group and k1 + 1 == k and t != OPEN_END)
            parts = step["start"][:, g].sum() - step["end"][:, g].sum()
            assert parts * 1000 == pytest.approx(starts - ends, abs=1e-6)
            km = next(x["km"] for x in timeline["groups"] if x["id"] == group)
            assert parts == pytest.approx(km[k] - km[k - 1], abs=0.051)


def test_report_names_the_retagging(build):
    _, result = rows_of(build, rows=[1])
    text = changes.report(result, 0, 3)
    assert "Schutzstreifen → Radfahrstreifen: 0,2 km" in text


def test_a_retagging_next_door_is_no_replacement(build):
    steps, _ = rows_of(build, rows=[11])
    assert steps[0] == {("start", "added", "Getrennter Radweg"): 200}
    assert steps[1] == {("start", "replaced", "Schutzstreifen"): 200, ("end", "replaced", "Radfahrstreifen"): 200}


def test_the_popup_file_says_what_was_at_the_place(build):
    changes.write_changes(build / "versions.parquet", build / "timeline.json", build / "changes.parquet")
    write_spans(build / "versions.parquet", build / "timeline.json", build / "spans.bin", build / "changes.parquet")
    timeline = json.loads((build / "timeline.json").read_text())
    code = {c: i for i, c in enumerate(timeline["categories"])}
    data = (build / "spans.bin").read_bytes()
    rows = duckdb.connect().execute(
        f"SELECT id, k0, vid FROM read_parquet('{build / 'versions.parquet'}') ORDER BY id, k0").fetchall()
    first = {}
    last = {}
    for obj, _, vid in rows:
        first.setdefault(obj, vid)
        last[obj] = vid
    place = lambda vid: SPAN_RECORD.unpack_from(data, vid * SPAN_RECORD.size)[4:]  # noqa: E731
    # Moved from the road's tags to a way of its own: before, a side of a road.
    assert place(first["way/21"])[0] == code[TRACK] | ON_ROAD
    assert place(last["way/20/cycleway/right"])[1] == code[TRACK]
    # Nothing there before, nothing left after; something else beside it.
    assert place(first["way/40"])[0] == PLACE_EMPTY
    assert place(last["way/50"])[1] == PLACE_EMPTY
    assert place(first["way/61"])[0] == PLACE_BESIDE
    # In the first snapshot nothing is known about before, and a run still open has no after.
    assert place(first["way/60/cycleway/right"]) == (NO_CATEGORY, NO_CATEGORY)


def test_each_area_splits_like_the_region(build, monkeypatch):
    """The split goes into the areas' numbers: per area it adds up to the change of
    its km, and two halves add up to the whole."""
    changes.write_changes(build / "versions.parquet", build / "timeline.json", build / "changes.parquet")
    whole = shapely.box(13.39, 52.49, 13.42, 52.63)
    middle = 13.4 + 100 / (M_PER_DEG * np.cos(np.radians(52.55)))
    boundaries = [
        ("a3", {"boundary": "administrative", "admin_level": "4", "name": "Teststadt"}, whole),
        ("a5", {"boundary": "administrative", "admin_level": "9", "name": "West"},
         shapely.box(13.39, 52.49, middle, 52.63)),
        ("a7", {"boundary": "administrative", "admin_level": "9", "name": "Ost"},
         shapely.box(middle, 52.49, 13.42, 52.63)),
    ]
    monkeypatch.setattr(areas, "read_boundaries", lambda pbf, levels=None: boundaries)
    region = Region("test", "Teststadt", 1, "", levels=("9",))
    areas.build_areas("unused.osm.pbf", build / "versions.parquet", build / "timeline.json", build, region,
                      changes_parquet=build / "changes.parquet")
    index = json.loads((build / "areas" / "index.json").read_text())
    bundle = json.loads((build / "areas" / "_all.json").read_text())["areas"]
    groups = index["groups"]

    def split_of(slug):
        out = {}
        for step, row, *parts in bundle[slug]["chg"]:
            out[(step, row)] = np.array(parts)
        return out

    top = split_of("teststadt")
    for (step, row), parts in top.items():
        km = bundle["teststadt"]["km"].get(groups[row], [0] * len(index["snapshots"]))
        assert parts[:3].sum() - parts[3:].sum() == pytest.approx(km[step] - km[step - 1], abs=0.03)
    halves = [split_of("bezirk-west"), split_of("bezirk-ost")]
    for key, parts in top.items():
        assert sum(h.get(key, np.zeros(6)) for h in halves) == pytest.approx(parts, abs=0.03)
    # The retagging of way/10 at the third snapshot, by what it replaced.
    assert [2, groups.index("Radfahrstreifen"), groups.index("Schutzstreifen"), 0.2] in bundle["teststadt"]["fl"]
