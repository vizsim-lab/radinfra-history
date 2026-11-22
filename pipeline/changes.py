"""Where the km change: at new places, or at the same places mapped differently.

    uv run python -m pipeline.changes --region germany --from 2020-01-01
    uv run python -m pipeline.changes --region germany --bbox 13.08,52.33,13.77,52.68 --csv berlin.csv

Between two snapshots a legend row's km change by exactly what starts there minus
what ends there (pipeline/versions.py). Which object starts or ends says little
about why: a way that is split, redrawn, retagged, or moved from its road's tags
to a way of its own ends one version and starts another, often under another id.
So this looks at places. Every metre that starts at step k (snapshot k-1 to k) is

    replaced  near something that was there before and ended at k: the same place,
              retagged or redrawn, or rebuilt, which the data cannot tell apart
    added     else near something else that was there before and stays: more at a
              place that already had cycling infrastructure in OSM
    new       else: a place with nothing before, newly built or newly mapped

and every metre that ends at step k, the other way round:

    replaced  near something that started at k
    reduced   else near something else that stays
    gone      else: nothing left at the place, removed in the street or in OSM

So for every legend row  Δ = replaced in − replaced out + added − reduced + new − gone,
to the metre, and the change between any two snapshots splits the same way, as the
sum over the steps between them.

"Near" is within TOLERANCE_M. A road's sides share its centreline, so each side is
first moved SIDE_OFFSET_M to its side: the two sides of a road are then twice that
apart and a side meets another side only within SIDE_TOLERANCE_M, so a new second
side is not taken for the first. A way of its own lies where it is drawn, a few
metres beside the road whose tags it replaces.

A cycleway is often drawn as a way of its own a quarter before or after its road's
tags say `separate`. So `replaced` also looks WINDOW steps either way, but only at
objects that disappeared or appeared there (pipeline/versions.py: no `nc`, no
`pc`), not at edits or retaggings, which leave the place as covered as before.
The newest step has no step after it yet and can change with the next snapshot.

Every build has the split of each version in changes.parquet (write_changes):

    vid                       the version
    s_rep, s_add, s_new       shares of its length at its start: replaced, added, new
                              (empty for versions of the first snapshot)
    s_from, s_road            the category it replaced, most of it, and whether
                              that was a side of a road
    e_rep, e_red, e_gone      the same at its end (empty while it lasts)
    e_to, e_road              what replaced it

pipeline/areas.py turns them into each area's numbers, pipeline/versions.py
(write_spans) into the popup's "vorher an dieser Stelle". On its own, this module
prints the split for a build or a box of it and can write every step as CSV. On a
synthetic Germany (1.5 million lines, 80,000 changes a step) a step takes about 15 s.
"""

import argparse
import csv
import json
from pathlib import Path

import duckdb
import numpy as np
import shapely

from .builds import Region
from .style import drawn_categories, legend_groups
from .versions import OPEN_END, _explode, counted_m

TOLERANCE_M = 20.0
SIDE_OFFSET_M = 5.0
SIDE_TOLERANCE_M = 4.0
WINDOW = 1
# Lines are looked at in pieces of at most this length.
SEGMENT_M = 5.0

START_KINDS = ("replaced", "added", "new")
END_KINDS = ("replaced", "reduced", "gone")

# For the printout; the page has its own names (site/config.js).
LABELS = {
    "needsClarification-details": "Führungsform unklar",
    "Gemeinsamer Fahrstreifen mit Kfz Markiert": "Gem. Fahrstreifen mit Kfz (markiert)",
    "Gemeinsamer Fahrstreifen mit Bus": "Gem. Fahrstreifen mit Bus",
    "Fahrradstrasse Mischverkehr": "Fahrradstraße (Mischverkehr)",
    "Fahrradstrasse keine Kfz": "Fahrradstraße (keine Kfz)",
    "Gehweg Rad frei -details": "Gehweg, Rad frei",
    "Gemeinsamer Geh u Radweg": "Gemeinsamer Geh- und Radweg",
    "Geschuetzter Radfahrstreifen": "Geschützter Radfahrstreifen",
}

_NEVER = 1 << 30


def _project(geoms, lat0):
    """Metres east and north, equirectangular about lat0: across Germany within a
    few per cent, which is plenty for a tolerance of metres."""
    scale = np.array([111_320.0 * np.cos(np.radians(lat0)), 110_574.0])
    return shapely.transform(geoms, lambda c: c * scale)


def _placed(geoms, side):
    """Each side of a road moved SIDE_OFFSET_M to its side (shapely offsets to the
    left of the line's direction for a positive distance); ways of their own stay."""
    out = geoms.copy()
    for value, distance in (("l", SIDE_OFFSET_M), ("r", -SIDE_OFFSET_M)):
        rows = np.flatnonzero(side == value)
        if not len(rows):
            continue
        moved = shapely.offset_curve(geoms[rows], distance, quad_segs=2)
        bad = shapely.is_missing(moved) | shapely.is_empty(moved)
        moved[bad] = geoms[rows][bad]
        out[rows] = moved
    return out


def _samples(geoms):
    """Midpoint and length of every piece of every line, pieces at most SEGMENT_M
    long, and which line each piece belongs to."""
    if not len(geoms):
        return np.empty(0, dtype=object), np.empty(0), np.empty(0, dtype=np.int64)
    parts, part_of = _explode(shapely.segmentize(geoms, SEGMENT_M))
    coords, coord_part = shapely.get_coordinates(parts, return_index=True)
    same = coord_part[1:] == coord_part[:-1]
    a, b = coords[:-1][same], coords[1:][same]
    return shapely.points((a + b) / 2), np.hypot(*(b - a).T), part_of[coord_part[:-1][same]]


class Places:
    """Lines to measure against: for each point, the nearest within its tolerance."""

    def __init__(self, rows, placed, is_side):
        self.trees = []
        for side in (True, False):
            sub = rows[is_side[rows] == side]
            if len(sub):
                self.trees.append((side, sub, shapely.STRtree(placed[sub])))

    def nearest(self, points, point_is_side):
        """Row of the nearest line within tolerance, or -1."""
        best = np.full(len(points), -1, dtype=np.int64)
        best_d = np.full(len(points), np.inf)
        for side, sub, tree in self.trees:
            for query_side in (True, False):
                q = np.flatnonzero(point_is_side == query_side)
                if not len(q):
                    continue
                tol = SIDE_TOLERANCE_M if side and query_side else TOLERANCE_M
                (pi, ti), dist = tree.query_nearest(points[q], max_distance=tol, return_distance=True,
                                                    all_matches=False)
                pi = q[pi]
                better = dist < best_d[pi]
                best[pi[better]] = sub[ti[better]]
                best_d[pi[better]] = dist[better]
        return best


def _classify(rows, placed, is_side, ci, n_cat, replacements, cover):
    """How each line of `rows` splits by length into (replaced, added or reduced,
    new or gone), and the category (index into `ci`'s list) of what it replaced or
    was replaced by, the one with the most metres, or -1, and whether that was a side
    of a road."""
    n = len(rows)
    frac = np.zeros((n, 3))
    frac[:, 2] = 1.0
    cat = np.full(n, -1, dtype=np.int64)
    road = np.zeros(n, dtype=bool)
    if not n:
        return frac, cat, road
    points, length, owner = _samples(placed[rows])
    side = is_side[rows][owner]
    match = replacements.nearest(points, side)
    kind = np.where(match >= 0, 0, 2)
    rest = np.flatnonzero(kind == 2)
    if len(rest):
        kind[rest[cover.nearest(points[rest], side[rest]) >= 0]] = 1
    parts = np.stack([np.bincount(owner[kind == k], weights=length[kind == k], minlength=n) for k in range(3)],
                     axis=1)
    total = parts.sum(axis=1)
    # A line too short to sample stays `new` or `gone`: it is at most centimetres.
    sampled = total > 0
    frac[sampled] = parts[sampled] / total[sampled, None]
    sel = np.flatnonzero(kind == 0)
    if len(sel):
        r, m, w = owner[sel], match[sel], length[sel]
        by_cat = np.bincount(r * n_cat + ci[m], weights=w, minlength=n * n_cat).reshape(n, n_cat)
        has = by_cat.max(axis=1) > 0
        cat[has] = by_cat[has].argmax(axis=1)
        on_road = np.bincount(r, weights=w * is_side[m], minlength=n)
        road = has & (on_road >= 0.5 * parts[:, 0])
    return frac, cat, road


def classify(versions_parquet, timeline_json, bbox=None, window=WINDOW):
    """Every version's start and end, split as the module says, in vid order."""
    timeline = json.loads(Path(timeline_json).read_text(encoding="utf-8"))
    n = len(timeline["snapshots"])
    categories = timeline.get("categories") or sorted(drawn_categories())
    code = {c: i for i, c in enumerate(categories)}

    cols = duckdb.connect().execute(f"""
        SELECT vid, category, side, k0, k1, t, dirs, length_m,
               cf = f AND pc IS NULL AS appears, ct = t AND nc IS NULL AS vanishes,
               geometry::BLOB AS wkb
        FROM read_parquet('{versions_parquet}') ORDER BY vid
    """).fetchnumpy()
    geoms = shapely.from_wkb(np.asarray([bytes(b) for b in cols.pop("wkb")], dtype=object))
    cols = {k: np.asarray(v) for k, v in cols.items()}
    if bbox is not None:
        keep = shapely.intersects(geoms, shapely.box(*bbox))
        geoms = geoms[keep]
        cols = {k: v[keep] for k, v in cols.items()}
    unknown = sorted(set(cols["category"]) - set(code))
    if unknown:
        raise SystemExit(f"{versions_parquet}: categories the timeline does not list: {unknown}")

    ci = np.array([code[c] for c in cols["category"]], dtype=np.int64)
    is_side = np.isin(cols["side"], ["l", "r"])
    k0, k1 = cols["k0"].astype(np.int64), cols["k1"].astype(np.int64)
    is_open = cols["t"] == OPEN_END
    # The step a version starts at (none for the first snapshot: it was there) and
    # the step it ends at (none while it lasts); whether the object appears or
    # vanishes with it, rather than having been or staying there in another version.
    start = np.where(k0 >= 1, k0, -_NEVER)
    end = np.where(~is_open, k1 + 1, _NEVER)
    appears = cols["appears"].astype(bool) & (k0 >= 1)
    vanishes = cols["vanishes"].astype(bool) & ~is_open

    if len(geoms):
        bounds = shapely.bounds(geoms)
        lat0 = float(np.median((bounds[:, 1] + bounds[:, 3]) / 2))
    else:
        lat0 = 51.0
    placed = _placed(_project(geoms, lat0), cols["side"])

    # What is there at a snapshot, for two steps in a row: after one, before the next.
    present = {}

    def present_at(k):
        if k not in present:
            for old in [j for j in present if j < k - 1]:
                del present[old]
            present[k] = Places(np.flatnonzero((k0 <= k) & (k1 >= k)), placed, is_side)
        return present[k]

    count = len(geoms)
    out = {
        "categories": categories,
        "vid": cols["vid"].astype(np.int64), "ci": ci, "k0": k0, "k1": k1, "open": is_open,
        "metres": counted_m(cols["length_m"].astype(float), cols["dirs"]),
        "start": np.full((count, 3), np.nan), "start_cat": np.full(count, -1, dtype=np.int64),
        "start_road": np.zeros(count, dtype=bool),
        "end": np.full((count, 3), np.nan), "end_cat": np.full(count, -1, dtype=np.int64),
        "end_road": np.zeros(count, dtype=bool),
    }
    for k in range(1, n):
        # What a started metre can replace: whatever ended at k, and what vanished
        # within the window; what an ended metre can be replaced by, the same way round.
        before = Places(np.flatnonzero((k0 <= k - 1) & ((end == k) | (vanishes & (np.abs(end - k) <= window)))),
                        placed, is_side)
        after = Places(np.flatnonzero((k1 >= k) & ((start == k) | (appears & (np.abs(start - k) <= window)))),
                       placed, is_side)
        for rows, which, places, cover in ((np.flatnonzero(k0 == k), "start", before, present_at(k - 1)),
                                           (np.flatnonzero(end == k), "end", after, present_at(k))):
            frac, cat, road = _classify(rows, placed, is_side, ci, len(categories), places, cover)
            out[which][rows], out[f"{which}_cat"][rows], out[f"{which}_road"][rows] = frac, cat, road
    return out


def analyse(versions_parquet, timeline_json, bbox=None, window=WINDOW):
    """Per step and legend row: km that start (replaced, added, new) and end
    (replaced, reduced, gone), and the replaced km between legend rows, by what
    each line replaced most."""
    timeline = json.loads(Path(timeline_json).read_text(encoding="utf-8"))
    snapshots = timeline["snapshots"]
    n = len(snapshots)
    groups = legend_groups()
    n_groups = len(groups)
    c = classify(versions_parquet, timeline_json, bbox=bbox, window=window)
    group_of = {cat: i for i, g in enumerate(groups) for cat in g["categories"]}
    group_of_code = np.array([group_of[cat] for cat in c["categories"]] + [-1], dtype=np.int64)
    gi = group_of_code[c["ci"]]

    def per_step(rows, step, frac, cat):
        kinds = np.zeros((n + 1, 3, n_groups))
        flows = np.zeros((n + 1, n_groups, n_groups))
        km = c["metres"][rows, None] * frac[rows] / 1000.0
        for i in range(3):
            np.add.at(kinds, (step[rows], i, gi[rows]), km[:, i])
        matched = cat[rows] >= 0
        np.add.at(flows, (step[rows][matched], gi[rows][matched], group_of_code[cat[rows][matched]]),
                  km[matched, 0])
        return kinds, flows

    started = np.flatnonzero(c["k0"] >= 1)
    ended = np.flatnonzero(~c["open"])
    s_kinds, s_flows = per_step(started, c["k0"], c["start"], c["start_cat"])
    e_kinds, e_flows = per_step(ended, c["k1"] + 1, c["end"], c["end_cat"])
    steps = [{
        "date": snapshots[k]["date"],
        "start": s_kinds[k], "end": e_kinds[k], "flow_in": s_flows[k], "flow_out": e_flows[k],
        "provisional": k + window > n - 1,
    } for k in range(1, n)]
    return {"groups": [g["id"] for g in groups], "snapshots": [s["date"] for s in snapshots], "steps": steps}


def write_changes(versions_parquet, timeline_json, out_parquet, window=WINDOW):
    """The split of every version, for the areas and the popup (module docstring)."""
    c = classify(versions_parquet, timeline_json, window=window)
    names = np.array(list(c["categories"]) + [None], dtype=object)
    has_start, has_end = c["k0"] >= 1, ~c["open"]
    start, end = c["start"].astype(np.float32), c["end"].astype(np.float32)
    con = duckdb.connect()
    con.register("c", {
        "vid": c["vid"].astype(np.int32),
        # NaN, where there is no start or end, is NULL in the file.
        "s_rep": start[:, 0], "s_add": start[:, 1], "s_new": start[:, 2],
        "s_from": names[c["start_cat"]], "s_road": c["start_road"] & has_start,
        "e_rep": end[:, 0], "e_red": end[:, 1], "e_gone": end[:, 2],
        "e_to": names[c["end_cat"]], "e_road": c["end_road"] & has_end,
    })
    out_parquet = Path(out_parquet)
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM c ORDER BY vid) TO '{out_parquet}' (FORMAT parquet, COMPRESSION zstd)")

    def km(rows, frac):
        return (c["metres"][rows, None] * frac[rows]).sum(axis=0) / 1000.0

    s_km, e_km = km(has_start, c["start"]), km(has_end, c["end"])
    print(f"  {out_parquet}: started {s_km[0]:,.0f} km replaced, {s_km[1]:,.0f} km added, {s_km[2]:,.0f} km new; "
          f"ended {e_km[0]:,.0f} km replaced, {e_km[1]:,.0f} km reduced, {e_km[2]:,.0f} km gone", flush=True)
    return out_parquet


def summarise(result, first, last):
    """Sum the steps after snapshot `first` up to and including `last` (indices)."""
    steps = result["steps"][first:last]
    n_groups = len(result["groups"])
    start = sum((s["start"] for s in steps), np.zeros((3, n_groups)))
    end = sum((s["end"] for s in steps), np.zeros((3, n_groups)))
    flow_in = sum((s["flow_in"] for s in steps), np.zeros((n_groups, n_groups)))
    return start, end, flow_in


def _num(v, digits=1):
    """German digits: 1.234,5."""
    return f"{v:,.{digits}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _fmt(v):
    return "±0" if abs(v) < 0.05 else f"{'+' if v > 0 else '−'}{_num(abs(v))}"


def _label(group_id):
    return LABELS.get(group_id, group_id)


def report(result, first, last, top=8):
    """The change between two snapshots as a table, its largest retaggings, and the
    hints the page could give next to its change table."""
    groups = result["groups"]
    start, end, flow_in = summarise(result, first, last)
    replaced, added, placed = (start[i] - end[i] for i in range(3))
    delta = start.sum(axis=0) - end.sum(axis=0)
    a, b = result["snapshots"][first], result["snapshots"][last]
    lines = [
        f"Veränderung {a} → {b} in km",
        "  ersetzt       dieselbe Stelle, anders erfasst: umgetaggt, neu gezeichnet, separiert (oder umgebaut)",
        "  ergänzt       an Stellen mit Radinfrastruktur dazu oder weg (zweiter Weg, Dublette)",
        "  neue Stellen  wo vorher nichts war (neu gebaut oder erstmals kartiert), minus wo nichts mehr ist",
        "",
        f"{'':38} {'Δ':>9} {'ersetzt':>9} {'ergänzt':>9} {'Stellen':>9}",
    ]
    shown = [g for g in reversed(range(len(groups))) if abs(delta[g]) >= 0.05 or start[:, g].sum() >= 0.05]
    for g in shown:
        lines.append(f"{_label(groups[g]):38} {_fmt(delta[g]):>9} {_fmt(replaced[g]):>9} "
                     f"{_fmt(added[g]):>9} {_fmt(placed[g]):>9}")
    lines.append(f"{'Summe':38} {_fmt(delta.sum()):>9} {_fmt(replaced.sum()):>9} "
                 f"{_fmt(added.sum()):>9} {_fmt(placed.sum()):>9}")
    lines.append("")
    lines.append(f"Brutto: {_num(start[0].sum(), 0)} km ersetzt, {_num(start[1].sum(), 0)} km an erfassten "
                 f"Stellen dazu, {_num(start[2].sum(), 0)} km an neuen Stellen; {_num(end[1].sum(), 0)} km an weiter "
                 f"erfassten Stellen weg, an {_num(end[2].sum(), 0)} km ist nichts mehr.")

    between = flow_in.copy()
    np.fill_diagonal(between, 0)
    if between.sum() >= 0.05:
        lines += ["", "Größte Wechsel an derselben Stelle (vorher → nachher):"]
        for flat in np.argsort(-between, axis=None)[:top]:
            to, frm = np.unravel_index(flat, between.shape)
            if between[to, frm] < 0.05:
                break
            lines.append(f"  {_label(groups[frm])} → {_label(groups[to])}: {_num(between[to, frm])} km")

    hints = []
    for g in sorted(shown, key=lambda g: -abs(delta[g])):
        if abs(delta[g]) < 1:
            continue
        if delta[g] > 0 and replaced[g] >= 0.5 * delta[g]:
            sources = sorted(((between[g, h], h) for h in range(len(groups)) if between[g, h] >= 0.5), reverse=True)
            names = ", ".join(f"{_label(groups[h])} {_num(v, 0)} km" for v, h in sources[:2])
            hints.append(f"„{_label(groups[g])}“ {_fmt(delta[g])} km: davon {_num(replaced[g], 0)} km an Stellen, die "
                         f"vorher anders erfasst waren{f' ({names})' if names else ''}; an neuen Stellen "
                         f"{_fmt(placed[g])} km.")
        elif delta[g] < 0 and -replaced[g] >= 0.5 * -delta[g]:
            hints.append(f"„{_label(groups[g])}“ {_fmt(delta[g])} km: davon {_num(-replaced[g], 0)} km jetzt anders "
                         f"erfasst, nicht verschwunden; Stellen ohne Radinfrastruktur danach {_fmt(placed[g])} km.")
    if hints:
        lines += ["", "Hinweise, wie die Seite sie geben könnte:", *(f"  {h}" for h in hints[:3])]
    if last > first and result["steps"][last - 1]["provisional"]:
        lines.append(f"\n(Der Schritt bis {b} ist vorläufig: ihm fehlt der Stand danach.)")
    return "\n".join(lines)


def write_csv(result, path):
    groups = result["groups"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        out = csv.writer(fh)
        out.writerow(["date", "group", "delta_km",
                      *[f"start_{k}" for k in START_KINDS], *[f"end_{k}" for k in END_KINDS]])
        for step in result["steps"]:
            for g, name in enumerate(groups):
                s, e = step["start"][:, g], step["end"][:, g]
                if s.sum() < 1e-4 and e.sum() < 1e-4:
                    continue
                out.writerow([step["date"], name, round(s.sum() - e.sum(), 4),
                              *[round(v, 4) for v in s], *[round(v, 4) for v in e]])


def main():
    parser = argparse.ArgumentParser(prog="python -m pipeline.changes", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default="germany", help="its current build in site/data/<region>/")
    parser.add_argument("--build", type=Path, help="a build folder instead (with versions.parquet, timeline.json)")
    parser.add_argument("--from", dest="start", help="first snapshot date (default: the first)")
    parser.add_argument("--to", dest="end", help="last snapshot date (default: the newest)")
    parser.add_argument("--bbox", help="west,south,east,north: only lines that touch it")
    parser.add_argument("--window", type=int, default=WINDOW)
    parser.add_argument("--csv", type=Path, help="every step and legend row as CSV")
    args = parser.parse_args()
    build = args.build
    if build is None:
        region = Region(Path("site/data") / args.region)
        current = region.current()
        if current is None:
            raise SystemExit(f"no build in site/data/{args.region} (scripts/fetch_data.sh {args.region})")
        build = region.build_dir(current)
    bbox = tuple(float(v) for v in args.bbox.split(",")) if args.bbox else None
    result = analyse(build / "versions.parquet", build / "timeline.json", bbox=bbox, window=args.window)
    dates = result["snapshots"]

    def index_of(value, default):
        """The newest snapshot on or before a date."""
        return max((i for i, d in enumerate(dates) if d <= value), default=0) if value else default

    first, last = index_of(args.start, 0), index_of(args.end, len(dates) - 1)
    print(report(result, first, last))
    if args.csv:
        write_csv(result, args.csv)
        print(f"\n{args.csv}: every step and legend row")


if __name__ == "__main__":
    main()
