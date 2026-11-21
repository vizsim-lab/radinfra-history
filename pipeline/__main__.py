"""radinfra-history pipeline.

    uv run python -m pipeline all --osh berlin-internal.osh.pbf --start 2016-01-01 --step 3
    uv run python -m pipeline all --pbf data/dated/berlin-2*.osm.pbf
    uv run python -m pipeline update --pbf berlin-latest.osm.pbf

Steps, each also runnable on its own: snapshots -> classify -> versions -> areas -> tiles.
`update` adds one newer extract to existing versions (the quarterly workflow).
Every command from `versions` on makes a new build in site/data/<slug>/
(pipeline/builds.py); `areas` or `tiles` alone take the rest from the current one.
"""

import argparse
import glob
import json
import os
import time
from datetime import date
from pathlib import Path

import duckdb

from . import regions
from . import snapshots as snaps
from .areas import build_areas
from .builds import REQUIRED, Region
from .classify import classify_all, classify_history, classify_snapshot, date_key
from .tiles import build_tiles
from .versions import append_snapshot, build_versions, write_spans

DATA = Path("data")
SITE = Path("site")

# Files of the current build a command takes over: what it does not make anew and
# what does not depend on what it makes. New versions change everything else.
KEEP = {
    "areas": [name for name in REQUIRED if not name.startswith("areas.")],
    "tiles": [name for name in REQUIRED if not name.startswith("tiles")],
}


def _snapshot_files(args):
    files = [p for pattern in args.pbf for p in sorted(glob.glob(pattern))]
    if not files:
        raise SystemExit("no input: pass --osh or --pbf")
    return snaps.from_files(files)


def _classified(args):
    """Snapshots cut and classified: [(date, parquet)]."""
    if not args.osh:
        snapshot_files = _snapshot_files(args)
        print(f"[snapshots] {len(snapshot_files)}: {snapshot_files[0][0]} … {snapshot_files[-1][0]}", flush=True)
        print("[classify]", flush=True)
        return classify_all(snapshot_files, args.classified_dir, args.threads, args.force)
    plan = snaps.snapshot_plan(args.osh, snaps.parse_date(args.start), args.step,
                               end=snaps.parse_date(args.end) if args.end else None,
                               cache=args.snapshot_dir / "history.json")
    print(f"[snapshots] {len(plan)} from {args.osh}: {plan[0][0]} … {plan[-1][0]}", flush=True)
    print(f"[classify] one at a time, {args.jobs} in parallel", flush=True)
    # The newest snapshot stays: the areas take their boundaries from it.
    keep = [d for d, _ in plan] if args.keep_snapshots else [plan[-1][0]]
    return classify_history(args.osh, plan, args.snapshot_dir, args.classified_dir,
                            args.threads, args.jobs, args.force, keep)


def _coverage(args, region):
    """The area the snapshots cover: the history file's (or newest extract's) header,
    else what the current build or the region says."""
    source = args.osh or (max(_snapshot_files(args))[1] if args.pbf else None)
    found = snaps.history_bbox(source) if source else None
    if found is None and region.current():
        found = json.loads((region.build_dir(region.current()) / "timeline.json")
                           .read_text(encoding="utf-8")).get("coverage")
    return found or args.spec.coverage


def _existing_classified(directory):
    found = []
    for path in sorted(Path(directory).glob("*.parquet")):
        found.append((snaps.date_from_filename(path), path))
    if not found:
        raise SystemExit(f"no classified snapshots in {directory}")
    return found


def main():
    parser = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["all", "snapshots", "classify", "versions", "areas", "tiles", "update"])
    source = parser.add_argument_group("input (snapshots, classify, all)")
    source.add_argument("--osh", help="full-history .osh.pbf to cut snapshots from")
    source.add_argument("--start", default="2016-01-01", help="first snapshot date (default 2016-01-01)")
    source.add_argument("--step", type=int, default=3, help="months between snapshots (default 3)")
    source.add_argument("--end", help="last regular snapshot date (default: end of the history file)")
    source.add_argument("--pbf", nargs="*", default=[], help="dated extracts instead of --osh (globs ok)")
    source.add_argument("--date", help="update: date of the extract (default: from its header)")
    parser.add_argument("--region", "--slug", dest="region", default="berlin",
                        help=f"one of {', '.join(regions.REGIONS)} (pipeline/regions.py): "
                             "its folder in data/, site/data/ and the bucket")
    parser.add_argument("--snapshot-dir", type=Path, help="default data/<slug>/snapshots")
    parser.add_argument("--classified-dir", type=Path, help="default data/<slug>/classified")
    parser.add_argument("--threads", type=int, default=2, help="threads per OSMnexus run")
    parser.add_argument("--jobs", type=int, default=1,
                        help="snapshots cut and classified at the same time (--osh; memory is the limit)")
    parser.add_argument("--keep-snapshots", action="store_true",
                        help="--osh: keep every snapshot, not just the newest")
    parser.add_argument("--force", action="store_true", help="reclassify even if results exist")
    parser.add_argument("--boundaries-pbf", type=Path,
                        help="snapshot to take Bezirk/Ortsteil boundaries from (default: newest in --snapshot-dir)")
    parser.add_argument("--minzoom", type=int, default=9)
    parser.add_argument("--maxzoom", type=int, default=14)
    args = parser.parse_args()
    args.spec = regions.get(args.region)
    args.slug = args.spec.slug
    args.snapshot_dir = args.snapshot_dir or DATA / args.slug / "snapshots"
    args.classified_dir = args.classified_dir or DATA / args.slug / "classified"
    region = Region(SITE / "data" / args.slug)

    started = time.time()
    if args.command == "update":
        _update(args, region)
        print(f"done in {time.time() - started:.0f} s", flush=True)
        return

    if args.command == "snapshots":
        if not args.osh:
            raise SystemExit("snapshots: pass --osh")
        cut = snaps.cut_from_history(args.osh, args.snapshot_dir, snaps.parse_date(args.start), args.step,
                                     end=snaps.parse_date(args.end) if args.end else None)
        print(f"[snapshots] {len(cut)}: {cut[0][0]} … {cut[-1][0]}", flush=True)
        return
    classified = _classified(args) if args.command in ("all", "classify") else None
    if args.command == "classify":
        print(f"done in {time.time() - started:.0f} s", flush=True)
        return

    work = region.start(keep=KEEP.get(args.command, ()))
    versions_path, timeline_path = work / "versions.parquet", work / "timeline.json"
    if args.command in ("all", "versions"):
        print("[versions]", flush=True)
        build_versions(classified or _existing_classified(args.classified_dir),
                       versions_path, timeline_path, region=args.spec.name, coverage=_coverage(args, region))
        write_spans(versions_path, timeline_path, work / "spans.bin")

    if args.command in ("all", "areas"):
        print("[areas]", flush=True)
        boundaries = args.boundaries_pbf or max(
            Path(args.snapshot_dir).glob("*.osm.pbf"), key=snaps.date_from_filename)
        build_areas(boundaries, versions_path, timeline_path, work / "areas.json", work / "areas.geojson")

    if args.command in ("all", "tiles"):
        print("[tiles]", flush=True)
        build_tiles(versions_path, work / "tiles.pmtiles", args.minzoom, args.maxzoom)

    region.finish()
    print(f"done in {time.time() - started:.0f} s", flush=True)


def _github_output(**values):
    """Hand results to the next steps of a GitHub workflow, if there is one."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")


def _update(args, region):
    """One newer extract onto the versions of the current build: classify, append,
    then the areas (with the extract's boundaries) and the tiles from scratch."""
    files = [p for pattern in args.pbf for p in sorted(glob.glob(pattern))]
    if len(files) != 1:
        raise SystemExit("update: pass the one new extract with --pbf")
    pbf = Path(files[0])
    snapshot_date = snaps.parse_date(args.date) if args.date else snaps.extract_date(pbf)
    current = region.current()
    if current is None:
        raise SystemExit(f"{region.dir}: no build to update (scripts/fetch_data.sh first)")
    old = region.build_dir(current)
    last = date.fromisoformat(json.loads((old / "timeline.json").read_text(encoding="utf-8"))["snapshots"][-1]["date"])
    if snapshot_date <= last:
        print(f"[update] {pbf.name} is from {snapshot_date}, not after the last snapshot {last}: nothing to do",
              flush=True)
        _github_output(changed="false", date=snapshot_date.isoformat(), build=current)
        return
    print(f"[update] {pbf.name} from {snapshot_date}, after {last}", flush=True)
    coverage = json.loads((old / "timeline.json").read_text(encoding="utf-8")).get("coverage") or args.spec.coverage
    if coverage:
        snaps.check_coverage(pbf, coverage)
    else:
        print(f"  no coverage known for {args.slug}: not checked", flush=True)

    print("[classify]", flush=True)
    classified = classify_snapshot(snapshot_date, pbf, args.classified_dir / f"{snapshot_date.isoformat()}.parquet",
                                   args.threads)
    # The areas there now must all be there after, so a broken boundary cannot drop one.
    areas_now = old / "areas.json"
    keep_areas = ([a["slug"] for a in json.loads(areas_now.read_text(encoding="utf-8"))["areas"]]
                  if areas_now.is_file() else [])
    work = region.start(keep=("timeline.json", "versions.parquet"))
    versions_path, timeline_path = work / "versions.parquet", work / "timeline.json"
    print("[versions]", flush=True)
    append_snapshot(versions_path, timeline_path, classified, snapshot_date)
    write_spans(versions_path, timeline_path, work / "spans.bin")
    # A new or changed category needs an edit of the way's tags, so nearly all of
    # those ways should have been edited since the last snapshot. Far fewer would
    # mean that the extract differs, not OSM.
    ways = [i for (i,) in duckdb.connect().execute(
        f"SELECT DISTINCT osm_id FROM read_parquet('{versions_path}') WHERE cf = {date_key(snapshot_date)}"
    ).fetchall()]
    edited = snaps.edited_since(pbf, ways, last)
    print(f"  {len(ways):,} ways got a new category or were added; "
          + (f"{edited:,} of them edited in OSM since {last}" if edited is not None
             else "the extract has no timestamps to check them"), flush=True)
    print("[areas]", flush=True)
    build_areas(pbf, versions_path, timeline_path, work / "areas.json", work / "areas.geojson", keep=keep_areas)
    print("[tiles]", flush=True)
    build_tiles(versions_path, work / "tiles.pmtiles", args.minzoom, args.maxzoom)
    build = region.finish()
    _github_output(changed="true", date=snapshot_date.isoformat(), build=build)


if __name__ == "__main__":
    main()
