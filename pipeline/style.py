"""The radinfra.de (tilda) categories and how they group into legend rows.

The map draws radinfra.de's own "details" style (site/radinfra_details_style.json),
so that file is the single source of truth for which categories are drawn and how
they group: one style layer = one legend row = one colour. The statistics use the
same grouping, read from the same file, so the legend and the numbers next to it
cannot drift apart.

OSMnexus (configs/tilda) classifies into the same categories as tilda-geo but spells
them in snake_case; CATEGORY_MAP translates to the camelCase ids that radinfra.de's
tiles and style use. An unknown category raises instead of disappearing from the map.
"""

import json
from pathlib import Path

STYLE_PATH = Path(__file__).resolve().parent.parent / "site" / "radinfra_details_style.json"

# Invisible click target in the tilda style; nothing to draw or count.
SKIP_LAYERS = {"hitarea-bikelanes-details"}

# OSMnexus configs/tilda category -> tilda-geo category id.
CATEGORY_MAP = {
    "bicycle_road": "bicycleRoad",
    "bicycle_road_vehicle_destination": "bicycleRoad_vehicleDestination",
    "crossing": "crossing",
    "cycleway_adjoining": "cycleway_adjoining",
    "cycleway_isolated": "cycleway_isolated",
    "cycleway_adjoining_or_isolated": "cycleway_adjoiningOrIsolated",
    "cycleway_link": "cyclewayLink",
    "cycleway_on_highway_advisory": "cyclewayOnHighway_advisory",
    "cycleway_on_highway_exclusive": "cyclewayOnHighway_exclusive",
    "cycleway_on_highway_advisory_or_exclusive": "cyclewayOnHighway_advisoryOrExclusive",
    "cycleway_on_highway_between_lanes": "cyclewayOnHighwayBetweenLanes",
    "cycleway_on_highway_protected": "cyclewayOnHighwayProtected",
    "foot_and_cycleway_segregated_adjoining": "footAndCyclewaySegregated_adjoining",
    "foot_and_cycleway_segregated_isolated": "footAndCyclewaySegregated_isolated",
    "foot_and_cycleway_segregated_adjoining_or_isolated": "footAndCyclewaySegregated_adjoiningOrIsolated",
    "foot_and_cycleway_shared_adjoining": "footAndCyclewayShared_adjoining",
    "foot_and_cycleway_shared_isolated": "footAndCyclewayShared_isolated",
    "foot_and_cycleway_shared_adjoining_or_isolated": "footAndCyclewayShared_adjoiningOrIsolated",
    "footway_bicycle_yes_adjoining": "footwayBicycleYes_adjoining",
    "footway_bicycle_yes_isolated": "footwayBicycleYes_isolated",
    "footway_bicycle_yes_adjoining_or_isolated": "footwayBicycleYes_adjoiningOrIsolated",
    "needs_clarification": "needsClarification",
    "pedestrian_area_bicycle_yes": "pedestrianAreaBicycleYes",
    "shared_bus_lane_bike_with_bus": "sharedBusLaneBikeWithBus",
    "shared_bus_lane_bus_with_bike": "sharedBusLaneBusWithBike",
    "shared_motor_vehicle_lane": "sharedMotorVehicleLane",
}


def _categories_of(layer):
    """Category ids a style layer draws.

    Every styled layer in the tilda details style filters with
    ["match", ["get", "category"], <id or [ids]>, true, false]. Anything else is
    rejected, so a reworked upstream style fails loudly instead of silently
    dropping categories from the counts.
    """
    flt = layer.get("filter")
    if not (
        isinstance(flt, list)
        and len(flt) == 5
        and flt[0] == "match"
        and flt[1] == ["get", "category"]
        and flt[3] is True
        and flt[4] is False
    ):
        raise ValueError(f"unexpected filter in style layer {layer.get('id')!r}: {flt!r}")
    value = flt[2]
    return [value] if isinstance(value, str) else list(value)


def legend_groups(style_path=STYLE_PATH):
    """Styled layers in drawing order (bottom to top), with the categories each draws."""
    style = json.loads(Path(style_path).read_text(encoding="utf-8"))
    groups = []
    seen = {}
    for layer in style["layers"]:
        if layer["id"] in SKIP_LAYERS:
            continue
        categories = _categories_of(layer)
        for category in categories:
            if category in seen:
                raise ValueError(
                    f"category {category!r} is drawn by both {seen[category]!r} and {layer['id']!r}"
                )
            seen[category] = layer["id"]
        groups.append({
            "id": layer["id"],
            "categories": categories,
            "color": layer.get("paint", {}).get("line-color"),
        })
    return groups


def drawn_categories(style_path=STYLE_PATH):
    """Every tilda category id the style draws."""
    return {c for g in legend_groups(style_path) for c in g["categories"]}
