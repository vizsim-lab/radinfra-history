"""Align OSMnexus' tilda config with tilda-geo where they differ.

First, paths get no side objects.

tilda-geo never splits a path-like way (track, path, footway, cycleway, steps,
pedestrian) into left/right objects. It only folds `cycleway:*` tags into the
way itself (processing/topics/roads_bikelanes/bikelanes/transformations.lua,
"Don't transform paths only unnest tags prefixed with `cycleway`").

OSMnexus v0.1.0 does fold them in (transform with guard `sidepath_highway`), but
its side clones still fire on those ways. A segregated path tagged
`cycleway:surface=asphalt` + `footway:surface=paving_stones` then gets a
`cycleway/left` and a `cycleway/right` object next to itself, classified
`needs_clarification`: dashed "Führungsform unklar" lines on both sides of the
path. In Berlin that is about 4,300 objects and 480 km in the newest snapshot,
nearly all of that category.

This adds the missing guard to the four clone transforms.

Second, the way-level exclusions. tilda-geo drops a way before classifying it
when access forbids cycling (processing/topics/roads_bikelanes/helper/
exit_processing.lua with exclude_highways.lua): `access` in private / no /
delivery / permit, the same values in `bicycle` on a cycleway or `foot` on a
footway, and service roads other than alleys unless they explicitly allow bikes.
OSMnexus' `standard_exclude` has neither, so e.g. the fenced practice tracks of a
Jugendverkehrsschule (`highway=cycleway` + `access=private`) showed up as
"Führungsform unklar". About 130 objects in Berlin's newest snapshot.

Both patches are idempotent and fail if the upstream files no longer have the
shape they expect.

    python scripts/patch_osmnexus_config.py .tools/osmnexus/config
"""

import json
import sys
from pathlib import Path

GUARD = {"not": {"tag": "highway", "in": "sidepath_highway"}}
PREFIXES = {"cycleway", "sidewalk"}


def patch(config_dir):
    path = Path(config_dir) / "bikelanes" / "transforms.json"
    transforms = json.loads(path.read_text(encoding="utf-8"))
    clones = [
        t["clone"] for t in transforms["way"]
        if "clone" in t and t["clone"].get("annotate", {}).get("_prefix") in PREFIXES
    ]
    if len(clones) != 4:
        raise SystemExit(f"{path}: expected 4 side clones for {sorted(PREFIXES)}, found {len(clones)}")

    changed = 0
    for clone in clones:
        when = clone.get("when")
        if isinstance(when, dict) and GUARD in when.get("and", []):
            continue
        clone["when"] = {"and": [when, GUARD]} if when else GUARD
        changed += 1

    if changed:
        path.write_text(json.dumps(transforms, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{path}: side clones guarded against path-like highways ({changed} changed)")


FORBIDDEN_ACCESS = ["private", "no", "delivery", "permit"]


def _allows_bicycle_on(side_key):
    return {"and": [{"tag": side_key, "exists": True}, {"not": {"tag": side_key, "eq": "no"}}]}


# exclude_highways.by_access and .by_service in tilda-geo, as an OSMnexus condition.
TILDA_EXCLUSIONS = {"or": [
    {"tag": "access", "in": FORBIDDEN_ACCESS},
    {"and": [{"tag": "highway", "eq": "footway"}, {"tag": "foot", "in": FORBIDDEN_ACCESS}]},
    {"and": [{"tag": "highway", "eq": "cycleway"}, {"tag": "bicycle", "in": FORBIDDEN_ACCESS}]},
    {"and": [
        {"tag": "highway", "eq": "service"},
        {"tag": "service", "exists": True},
        {"not": {"tag": "service", "eq": "alley"}},
        {"not": {"tag": "bicycle", "eq": "designated"}},
        {"not": _allows_bicycle_on("bicycle:left")},
        {"not": _allows_bicycle_on("bicycle:right")},
        {"not": _allows_bicycle_on("bicycle:both")},
    ]},
]}


def patch_exclusions(config_dir):
    path = Path(config_dir) / "bikelanes" / "topic.json"
    topic = json.loads(path.read_text(encoding="utf-8"))
    current = topic.get("exclude_condition")
    if current == {"macro": "standard_exclude"}:
        topic["exclude_condition"] = {"or": [current, TILDA_EXCLUSIONS]}
        path.write_text(json.dumps(topic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{path}: access and service exclusions added")
    elif current == {"or": [{"macro": "standard_exclude"}, TILDA_EXCLUSIONS]}:
        print(f"{path}: access and service exclusions already present")
    else:
        raise SystemExit(f"{path}: unexpected exclude_condition {current!r}")


if __name__ == "__main__":
    config = sys.argv[1] if len(sys.argv) > 1 else ".tools/osmnexus/config"
    patch(config)
    patch_exclusions(config)
