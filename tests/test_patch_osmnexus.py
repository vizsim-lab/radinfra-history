"""The OSMnexus config patch: side clones must skip path-like ways."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "patch_osmnexus_config.py"
spec = importlib.util.spec_from_file_location("patch_osmnexus_config", SCRIPT)
patch_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch_module)


def clone(prefix, side, highway):
    return {"clone": {
        "when": {"not": {"tag": "highway", "eq": highway}},
        "annotate": {"_side": side, "_prefix": prefix},
        "id_suffix": f"{prefix}/{side}",
        "steps": [],
    }}


def write_config(tmp_path, transforms):
    (tmp_path / "bikelanes").mkdir()
    (tmp_path / "bikelanes" / "transforms.json").write_text(json.dumps({"way": transforms}))
    return tmp_path / "bikelanes" / "transforms.json"


def test_guards_all_four_clones_once(tmp_path):
    path = write_config(tmp_path, [
        {"unnest": "cycleway", "guard": {"tag": "highway", "in": "sidepath_highway"}},
        clone("cycleway", "left", "cycleway"), clone("cycleway", "right", "cycleway"),
        clone("sidewalk", "left", "footway"), clone("sidewalk", "right", "footway"),
    ])
    patch_module.patch(tmp_path)
    once = path.read_text()
    patch_module.patch(tmp_path)
    assert path.read_text() == once, "patch must be idempotent"

    clones = [t["clone"] for t in json.loads(once)["way"] if "clone" in t]
    for c in clones:
        assert patch_module.GUARD in c["when"]["and"]
        # The original condition is kept next to the guard.
        assert c["when"]["and"][0]["not"]["tag"] == "highway"


def test_refuses_unexpected_shape(tmp_path):
    write_config(tmp_path, [clone("cycleway", "left", "cycleway")])
    with pytest.raises(SystemExit):
        patch_module.patch(tmp_path)
