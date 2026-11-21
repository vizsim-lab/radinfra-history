"""Snapshots: the state of OSM at a series of dates.

Two sources, same result — one .osm.pbf per date, named <YYYY-MM-DD>.osm.pbf:

* a full-history file (.osh.pbf), cut with `osmium time-filter` at every step;
* ready-made dated extracts, e.g. Geofabrik's yearly berlin-YYMMDD.osm.pbf.

Snapshots cut from a history file are written without metadata (no user names,
uids, changesets, versions or timestamps), so nothing personal leaves this step.
"""

import json
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

from . import tools


def parse_date(value):
    return date.fromisoformat(value)


def add_months(d, months):
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def regular_dates(start, end, step_months):
    """First-of-month dates from `start`, every `step_months`, not after `end`."""
    if step_months < 1:
        raise ValueError("step_months must be >= 1")
    current = date(start.year, start.month, 1)
    if current < start:
        current = add_months(current, 1)
    dates = []
    while current <= end:
        dates.append(current)
        current = add_months(current, step_months)
    return dates


# The bounding box of the history file the snapshots were cut from (Geofabrik's
# Berlin extract). A later extract must cover it, or the areas would lose km.
HISTORY_BBOX = (13.08283, 52.33446, 13.762245, 52.6783)


def extract_date(pbf):
    """Date of a regular extract, from its replication timestamp (Geofabrik sets it)."""
    for option in ("osmosis_replication_timestamp", "timestamp"):
        result = tools.run([tools.osmium(), "fileinfo", "-g", f"header.option.{option}", pbf])
        stamp = result.stdout.strip()
        if stamp:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
    raise SystemExit(f"{pbf}: no timestamp in the header; pass --date")


def check_coverage(pbf, bbox=HISTORY_BBOX, tolerance=0.01):
    """The extract has to cover the area of the snapshots before it."""
    found = history_bbox(pbf)
    if found is None:
        print(f"  {pbf}: no bounding box in the header, coverage not checked", flush=True)
        return
    west, south, east, north = found
    if (west > bbox[0] + tolerance or south > bbox[1] + tolerance
            or east < bbox[2] - tolerance or north < bbox[3] - tolerance):
        raise SystemExit(f"{pbf}: covers {list(found)}, less than the snapshots before it ({bbox})")


def edited_since(pbf, way_ids, since):
    """How many of these ways the extract says were edited on or after `since`
    (their own timestamp: tags and node list, not moved nodes). None if the
    extract carries no timestamps."""
    if not way_ids:
        return 0
    with tempfile.TemporaryDirectory(prefix="edited-") as tmp:
        ids = Path(tmp) / "ids.txt"
        ids.write_text("".join(f"w{i}\n" for i in way_ids), encoding="utf-8")
        ways = Path(tmp) / "ways.osm.pbf"
        # Exit code 1: some ids not found (a way gone again); the others are written.
        tools.run([tools.osmium(), "getid", "-i", ids, pbf, "-o", ways, "--overwrite"], ok=(0, 1))
        opl = tools.run([tools.osmium(), "cat", ways, "-f", "opl"]).stdout
    stamps = re.findall(r" t(\d{4}-\d\d-\d\d)T", opl)
    if not stamps:
        return None
    return sum(date.fromisoformat(s) >= since for s in stamps)


def history_end(osh, cache=None):
    """Timestamp of the newest object version in a history file. Reading it means
    reading the whole file (minutes for Germany), so it is kept in `cache`, a JSON
    file, for as long as the history file is the same (size and mtime)."""
    osh = Path(osh)
    stat = osh.stat()
    key = {"file": osh.name, "size": stat.st_size, "mtime": stat.st_mtime}
    if cache and Path(cache).is_file():
        cached = json.loads(Path(cache).read_text(encoding="utf-8"))
        if {k: cached.get(k) for k in key} == key:
            return datetime.fromisoformat(cached["end"])
    result = tools.run([tools.osmium(), "fileinfo", "-e", "-g", "data.timestamp.last", osh])
    end = datetime.fromisoformat(result.stdout.strip().replace("Z", "+00:00"))
    if cache:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_text(json.dumps({**key, "end": end.isoformat()}) + "\n", encoding="utf-8")
    return end


def history_bbox(osh):
    """The bounding box in a file's header, (west, south, east, north), or None."""
    result = tools.run([tools.osmium(), "fileinfo", "-g", "header.boxes", osh])
    found = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", result.stdout)]
    return tuple(found[:4]) if len(found) >= 4 else None


def snapshot_plan(osh, start, step_months, end=None, cache=None):
    """The snapshots of a history file, [(date, at)] oldest first: every step from
    `start`, and the file's own end, so the map always reaches the newest state the
    file knows, even between two regular steps. `at` is the time to cut at, None for
    the end (osmium then takes the newest visible version of everything)."""
    file_end = history_end(osh, cache)
    last = min(end, file_end.date()) if end else file_end.date()
    dates = regular_dates(start, last, step_months)
    plan = [(d, f"{d.isoformat()}T00:00:00Z") for d in dates]
    if end is None and (not dates or file_end.date() > dates[-1]):
        plan.append((file_end.date(), None))
    return plan


def cut_snapshot(osh, at, path):
    """One snapshot, without metadata. Written next to `path` and renamed, so a
    snapshot that is there is complete."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    tools.run([
        tools.osmium(), "time-filter", osh, *([at] if at else []),
        "-o", part, "-f", "pbf,add_metadata=false", "--overwrite", "--no-progress",
    ])
    part.replace(path)
    return path


def cut_from_history(osh, out_dir, start, step_months, end=None):
    """Cut all snapshots out of a history file and keep them. Returns [(date, path)],
    oldest first. (`all` and `classify` cut them one at a time instead:
    classify.classify_history.)"""
    out_dir = Path(out_dir)
    return [(d, cut_snapshot(osh, at, out_dir / f"{d.isoformat()}.osm.pbf"))
            for d, at in snapshot_plan(osh, start, step_months, end, cache=out_dir / "history.json")]


_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_GEOFABRIK = re.compile(r"-(\d{2})(\d{2})(\d{2})\.osm\.pbf$")


def date_from_filename(path):
    """Date of a dated extract: 2021-01-01.osm.pbf or Geofabrik's berlin-210101.osm.pbf."""
    name = Path(path).name
    match = _ISO.search(name)
    if match:
        return date(*map(int, match.groups()))
    match = _GEOFABRIK.search(name)
    if match:
        yy, mm, dd = map(int, match.groups())
        return date(2000 + yy, mm, dd)
    raise ValueError(f"cannot read a date from {name!r}")


def from_files(paths):
    """[(date, path)] for ready-made dated extracts, oldest first."""
    snapshots = sorted((date_from_filename(p), Path(p)) for p in paths)
    seen = set()
    for d, p in snapshots:
        if d in seen:
            raise ValueError(f"two snapshots for {d}: check {p}")
        seen.add(d)
    return snapshots
