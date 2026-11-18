"""Helpers of the area statistics."""

import pytest
import shapely

from pipeline.areas import area_km2, slugify
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
