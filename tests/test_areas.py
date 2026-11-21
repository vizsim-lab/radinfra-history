"""Helpers of the area statistics."""

import pytest
import shapely

from pipeline.areas import area_km2, select_areas, slugify
from pipeline.versions import geometry_lengths_m


def test_slugify():
    assert slugify("Friedrichshain-Kreuzberg") == "friedrichshain-kreuzberg"
    assert slugify("Neukölln") == "neukoelln"
    assert slugify("Prenzlauer Berg") == "prenzlauer-berg"
    assert slugify("Weißensee") == "weissensee"
    assert slugify("Charlottenburg-Nord") == "charlottenburg-nord"


def test_line_crossing_the_boundary_counts_the_part_inside():
    square = shapely.box(13.40, 52.50, 13.41, 52.51)
    line = shapely.LineString([(13.395, 52.505), (13.405, 52.505)])
    full, inside = geometry_lengths_m([line, shapely.intersection(line, square)])
    assert inside == pytest.approx(full / 2, rel=1e-3)


def test_lengths_do_not_join_separate_parts():
    """A cut can return a collection of lines and points; the gap between two
    pieces must not be counted as if it were a segment."""
    piece = [(13.40, 52.5), (13.41, 52.5)]
    other = [(13.42, 52.5), (13.43, 52.5)]
    cut = shapely.GeometryCollection([shapely.Point(13.395, 52.5), shapely.MultiLineString([piece, other])])
    one = geometry_lengths_m([shapely.LineString(piece)])[0]
    assert geometry_lengths_m([cut])[0] == pytest.approx(2 * one, rel=1e-6)


def test_area_km2_of_a_known_box():
    # 1 km × 1 km near Berlin, built from metres per degree at 52.5° N.
    lat = 52.5
    dlon = 1 / (111.320 * 0.6087614)
    dlat = 1 / 110.574
    box = shapely.box(13.4, lat - dlat / 2, 13.4 + dlon, lat + dlat / 2)
    assert area_km2(box) == pytest.approx(1.0, rel=1e-3)


def boundary(relation, level, name, box):
    return (f"a{2 * relation + 1}", {"admin_level": level, "name": name}, shapely.box(*box))


# A country of two Länder, one a city (Berlin-like) with a Bezirk of two Ortsteile.
COUNTRY = [
    boundary(1, "2", "Testland", (0, 0, 20, 10)),
    boundary(2, "4", "Nordland", (0, 0, 10, 10)),
    boundary(3, "4", "Stadt", (10, 0, 20, 10)),
    boundary(4, "6", "Kreis Nord", (0, 0, 10, 10)),
    boundary(5, "8", "Lichtenberg", (0, 0, 5, 10)),
    boundary(6, "8", "Wiesenau", (5, 0, 10, 10)),
    boundary(7, "9", "Lichtenberg", (10, 0, 20, 10)),
    boundary(8, "10", "Lichtenberg", (10, 0, 15, 10)),
    boundary(9, "10", "Mitte", (15, 0, 20, 10)),
    boundary(10, "8", "Draußen", (30, 0, 40, 10)),   # outside the country
]


def test_select_areas_levels_and_parents():
    areas, _ = select_areas(COUNTRY, 1, ("4", "6", "8"), nested=(("stadt", ("9", "10")),))
    by = {a["name"] + "/" + a["level"]: a for a in areas}
    assert areas[0]["slug"] == "testland" and areas[0]["level"] == "Staat"
    assert "Draußen/Gemeinde" not in by
    assert by["Nordland/Land"]["parent"] == "testland"
    assert by["Kreis Nord/Kreis"]["parent"] == "nordland"
    assert by["Wiesenau/Gemeinde"]["parent"] == "kreis-nord"
    assert by["Lichtenberg/Bezirk"]["parent"] == "stadt"
    assert by["Mitte/Ortsteil"]["parent"] == by["Lichtenberg/Bezirk"]["slug"]


def test_shared_names_get_their_parent():
    """A Gemeinde and an Ortsteil both called Lichtenberg, at the same depth: both
    get their parent's slug; the Ortsteil keeps its own region's slug as an alias."""
    areas, aliases = select_areas(COUNTRY, 1, ("4", "6", "8"), nested=(("stadt", ("9", "10")),))
    slugs = {a["name"] + "/" + a["level"]: a["slug"] for a in areas}
    assert slugs["Lichtenberg/Gemeinde"] == "lichtenberg-kreis-nord"
    assert slugs["Lichtenberg/Ortsteil"] == "lichtenberg-bezirk-lichtenberg"
    assert slugs["Lichtenberg/Bezirk"] == "bezirk-lichtenberg"
    assert slugs["Wiesenau/Gemeinde"] == "wiesenau"
    assert aliases == {"lichtenberg": "lichtenberg-bezirk-lichtenberg"}


def test_the_higher_area_keeps_a_shared_name():
    """Land Bremen and the Stadt Bremen below it."""
    boundaries = [
        boundary(1, "2", "Testland", (0, 0, 20, 10)),
        boundary(2, "4", "Bremen", (0, 0, 10, 10)),
        boundary(3, "6", "Bremen", (0, 0, 5, 10)),
    ]
    areas, aliases = select_areas(boundaries, 1, ("4", "6"))
    assert [a["slug"] for a in areas] == ["testland", "bremen", "bremen-bremen"]
    assert aliases == {}


def test_a_city_region_as_berlin_has_it():
    """Berlin's own build: the Land on top, Bezirke `bezirk-…`, Ortsteile by name."""
    city = [b for b in COUNTRY if b[1]["admin_level"] in ("4", "9", "10")]
    areas, aliases = select_areas(city, 3, ("9", "10"))
    assert [(a["slug"], a["level"], a["parent"]) for a in areas] == [
        ("stadt", "Land", None),
        ("bezirk-lichtenberg", "Bezirk", "stadt"),
        ("lichtenberg", "Ortsteil", "bezirk-lichtenberg"),
        ("mitte", "Ortsteil", "bezirk-lichtenberg"),
    ]
    assert aliases == {}


def test_the_region_must_be_there():
    with pytest.raises(SystemExit, match="relation 99"):
        select_areas(COUNTRY, 99, ("4",))
