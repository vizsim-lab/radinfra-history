"""Align OSMnexus' tilda config with tilda-geo on paths: no side objects.

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

This adds the missing guard to the four clone transforms. It is idempotent and
fails if the upstream file no longer has the shape it expects.

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


if __name__ == "__main__":
    patch(sys.argv[1] if len(sys.argv) > 1 else ".tools/osmnexus/config")
