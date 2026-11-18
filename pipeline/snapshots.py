"""Snapshots: the state of OSM at a series of dates.

Two sources, same result — one .osm.pbf per date, named <YYYY-MM-DD>.osm.pbf:

* a full-history file (.osh.pbf), cut with `osmium time-filter` at every step;
* ready-made dated extracts, e.g. Geofabrik's yearly berlin-YYMMDD.osm.pbf.

Snapshots cut from a history file are written without metadata (no user names,
uids, changesets, versions or timestamps), so nothing personal leaves this step.
"""

import re
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
    result = tools.run([tools.osmium(), "fileinfo", "-g", "header.boxes", pbf])
    found = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", result.stdout)]
    if len(found) < 4:
        print(f"  {pbf}: no bounding box in the header, coverage not checked", flush=True)
        return
    west, south, east, north = found[:4]
    if (west > bbox[0] + tolerance or south > bbox[1] + tolerance
            or east < bbox[2] - tolerance or north < bbox[3] - tolerance):
        raise SystemExit(f"{pbf}: covers {found[:4]}, less than the snapshots before it ({bbox})")


def history_end(osh):
    """Timestamp of the newest object version in a history file."""
    result = tools.run([tools.osmium(), "fileinfo", "-e", "-g", "data.timestamp.last", osh])
    stamp = result.stdout.strip()
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def cut_from_history(osh, out_dir, start, step_months, end=None):
    """Cut snapshots out of a history file. Returns [(date, path)], oldest first.

    The last snapshot is the file's own end, so the map always reaches the newest
    state the history file knows, even between two regular steps.
    """
    osh = Path(osh)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    file_end = history_end(osh)
    last = min(end, file_end.date()) if end else file_end.date()
    dates = regular_dates(start, last, step_months)

    snapshots = []
    for d in dates:
        path = out_dir / f"{d.isoformat()}.osm.pbf"
        tools.run([
            tools.osmium(), "time-filter", osh, f"{d.isoformat()}T00:00:00Z",
            "-o", path, "-f", "pbf,add_metadata=false", "--overwrite", "--no-progress",
        ])
        snapshots.append((d, path))

    if end is None and (not dates or file_end.date() > dates[-1]):
        # No time argument: osmium takes the newest visible version of everything.
        d = file_end.date()
        path = out_dir / f"{d.isoformat()}.osm.pbf"
        tools.run([
            tools.osmium(), "time-filter", osh,
            "-o", path, "-f", "pbf,add_metadata=false", "--overwrite", "--no-progress",
        ])
        snapshots.append((d, path))
    return snapshots


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
