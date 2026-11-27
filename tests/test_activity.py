"""What changed in OSM per area and step (pipeline/areas.py: `act`, and by legend
row `cw` and `ch`), on hand-made snapshots: one case per row of streets, each far
from the others."""

import json
from datetime import date

import duckdb
import numpy as np
import pytest
import shapely

from pipeline import areas
from pipeline.classify import date_key
from pipeline.regions import Region
from pipeline.versions import EARTH_RADIUS_M, build_versions

DATES = [date(2020, 1, 1), date(2020, 4, 1), date(2020, 7, 1), date(2020, 10, 1)]
# As pipeline/versions.py measures: metres per degree on the sphere.
M_PER_DEG = EARTH_RADIUS_M * np.pi / 180
LAT0, LON0 = 52.5, 13.4


def lat_of(row):
    return LAT0 + 0.01 * row


def lon_at(x, lat):
    return LON0 + x / (M_PER_DEG * np.cos(np.radians(lat)))


def line(row, north_m=0.0, x0=0.0, x1=200.0):
    """An east-west line of row `row`, `north_m` north of the row's axis, from x0 to
    x1 metres east."""
    lat = lat_of(row) + north_m / M_PER_DEG
    return f"LINESTRING({lon_at(x0, lat):.9f} {lat:.9f}, {lon_at(x1, lat):.9f} {lat:.9f})"


ADVISORY, ADVISORY_OR_EXCLUSIVE = "cyclewayOnHighway_advisory", "cyclewayOnHighway_advisoryOrExclusive"
LANE, TRACK, ROAD = "cyclewayOnHighway_exclusive", "cycleway_adjoining", "bicycleRoad"
SHARED = "footAndCyclewayShared_isolated"
# Object id -> its state at each date: (category, wkt[, name]) or None.
STATES = {
    # Schutzstreifen -> Radfahrstreifen at the third snapshot: changed
    "way/10/cycleway/right": [(ADVISORY, line(1))] * 2 + [(LANE, line(1))] * 2,
    # another category of the same legend row (Schutzstreifen): not counted
    "way/20/cycleway/right": [(ADVISORY, line(2))] * 2 + [(ADVISORY_OR_EXCLUSIVE, line(2))] * 2,
    # came at the second snapshot, went at the third
    "way/30": [None] + [(ROAD, line(3))] + [None] * 2,
    # from the road's tags to a way of its own: went under one id, came under another
    "way/40/cycleway/right": [(TRACK, line(4))] * 2 + [None] * 2,
    "way/41": [None] * 2 + [(TRACK, line(4, -8))] * 2,
    # split at the second snapshot: the new part came, the shortened way is an edit
    "way/50": [(SHARED, line(5))] + [(SHARED, line(5, 0, 0, 120))] * 3,
    "way/51": [None] + [(SHARED, line(5, 0, 120, 200))] * 3,
    # renamed at the third snapshot: an edit, not counted
    "way/60": [(ROAD, line(6))] * 2 + [(ROAD, line(6), "Neuer Name")] * 2,
    # gone at the second snapshot and back at the fourth: went, then came
    "way/70": [(ROAD, line(7))] + [None] * 2 + [(ROAD, line(7))],
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
def bundle(tmp_path, monkeypatch):
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

    # The whole, and a west and an east half that cut every row at 100 m.
    south, north = lat_of(0), lat_of(8)
    middle = lon_at(100, (south + north) / 2)
    boundaries = [
        ("a3", {"boundary": "administrative", "admin_level": "4", "name": "Teststadt"},
         shapely.box(LON0 - 0.01, south, LON0 + 0.02, north)),
        ("a5", {"boundary": "administrative", "admin_level": "9", "name": "West"},
         shapely.box(LON0 - 0.01, south, middle, north)),
        ("a7", {"boundary": "administrative", "admin_level": "9", "name": "Ost"},
         shapely.box(middle, south, LON0 + 0.02, north)),
    ]
    monkeypatch.setattr(areas, "read_boundaries", lambda pbf, levels=None: boundaries)
    region = Region("test", "Teststadt", 1, "", levels=("9",))
    areas.build_areas("unused.osm.pbf", tmp_path / "versions.parquet", tmp_path / "timeline.json", tmp_path, region)
    return json.loads((tmp_path / "areas" / "_all.json").read_text())["areas"]


def activity(entry):
    """{step: (changed, came, went)} in metres."""
    return {step: tuple(round(v * 1000) for v in kinds) for step, *kinds in entry["act"]}


def test_each_step_counts_what_changed_category_came_and_went(bundle):
    act = activity(bundle["teststadt"])
    # Step 1: way/30 came, way/51 (the split-off part, 80 m) came, way/70 went.
    assert act[1] == (0, 200 + 80, 200)
    # Step 2: way/10 changed legend row; way/41 came, way/40's road side and way/30 went.
    # way/20 (within Schutzstreifen) and way/60 (renamed) are not counted.
    assert act[2] == (200, 200, 200 + 200)
    # Step 3: way/70 came back.
    assert act[3] == (0, 200, 0)
    # Nothing starts or ends at the first snapshot.
    assert 0 not in act


def test_the_halves_add_up_to_the_whole(bundle):
    whole = activity(bundle["teststadt"])
    west, east = activity(bundle["bezirk-west"]), activity(bundle["bezirk-ost"])
    for step, kinds in whole.items():
        halves = np.add(west.get(step, (0, 0, 0)), east.get(step, (0, 0, 0)))
        assert halves == pytest.approx(kinds, abs=2)
    # Step 1: way/30 lies half in each; the split-off part (120 to 200 m) in the east.
    assert west[1][1] == 100 and east[1][1] == 100 + 80


@pytest.fixture
def rows(bundle, tmp_path):
    """The legend rows the bundles number (index.json `groups`)."""
    return json.loads((tmp_path / "areas" / "index.json").read_text())["groups"]


def by_row(entry, rows):
    """({(step, row): (came, went)}, {(step, from, to): changed}) in metres."""
    came_went = {(k, rows[g]): (round(c * 1000), round(w * 1000)) for k, g, c, w in entry["cw"]}
    changes = {(k, rows[f], rows[t]): round(v * 1000) for k, f, t, v in entry["ch"]}
    return came_went, changes


def test_by_legend_row_what_came_went_and_where_it_changed_from(bundle, rows):
    came_went, changes = by_row(bundle["teststadt"], rows)
    # way/10, the one change of row; way/20 stays within Schutzstreifen.
    assert changes == {(2, "Schutzstreifen", "Radfahrstreifen"): 200}
    assert came_went == {
        (1, "Fahrradstrasse keine Kfz"): (200, 200),  # way/30 came, way/70 went
        (1, "Gemeinsamer Geh u Radweg"): (80, 0),  # the split-off part of way/50
        (2, "Getrennter Radweg"): (200, 200),  # way/41 came, way/40's road side went
        (2, "Fahrradstrasse keine Kfz"): (0, 200),  # way/30 went
        (3, "Fahrradstrasse keine Kfz"): (200, 0),  # way/70 came back
    }


@pytest.mark.parametrize("slug", ["teststadt", "bezirk-west", "bezirk-ost"])
def test_the_rows_add_up_to_act(bundle, rows, slug):
    came_went, changes = by_row(bundle[slug], rows)
    for step, (changed, came, went) in activity(bundle[slug]).items():
        assert sum(v for (k, *_), v in changes.items() if k == step) == pytest.approx(changed, abs=2)
        assert sum(c for (k, _), (c, _w) in came_went.items() if k == step) == pytest.approx(came, abs=2)
        assert sum(w for (k, _), (_c, w) in came_went.items() if k == step) == pytest.approx(went, abs=2)


def test_the_km_stay_as_they_were(bundle):
    km = bundle["teststadt"]["km"]
    assert km["Radfahrstreifen"] == [0.0, 0.0, 0.2, 0.2]
    assert km["Schutzstreifen"] == [0.4, 0.4, 0.2, 0.2]
    assert all(v >= 0 and str(v) != "-0.0" for series in km.values() for v in series)
