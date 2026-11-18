"""radinfra-history pipeline.

    uv run python -m pipeline all --osh berlin-internal.osh.pbf --start 2016-01-01 --step 3
    uv run python -m pipeline all --pbf data/dated/berlin-2*.osm.pbf
    uv run python -m pipeline update --pbf berlin-latest.osm.pbf

Steps, each also runnable on its own: snapshots -> classify -> versions -> areas -> tiles.
`update` adds one newer extract to existing versions (the quarterly workflow).
"""

import argparse
import glob
import json
import os
import time
from datetime import date
from pathlib import Path

import duckdb

from . import snapshots as snaps
from .areas import build_areas
from .classify import classify_all, classify_snapshot, date_key
from .tiles import build_tiles
from .versions import append_snapshot, build_versions, write_spans

DATA = Path("data")
SITE = Path("site")


def _snapshot_list(args):
    if args.osh:
        print(f"[snapshots] cutting {args.osh}", flush=True)
        return snaps.cut_from_history(
            args.osh, args.snapshot_dir, snaps.parse_date(args.start), args.step,
            end=snaps.parse_date(args.end) if args.end else None,
        )
    files = [p for pattern in args.pbf for p in sorted(glob.glob(pattern))]
    if not files:
        raise SystemExit("no input: pass --osh or --pbf")
    return snaps.from_files(files)


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
    parser.add_argument("--region", default="Berlin")
    parser.add_argument("--slug", default="berlin", help="file name stem for tiles and timeline")
    parser.add_argument("--snapshot-dir", default=DATA / "snapshots", type=Path)
    parser.add_argument("--classified-dir", default=DATA / "classified", type=Path)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--force", action="store_true", help="reclassify even if results exist")
    parser.add_argument("--boundaries-pbf", type=Path,
                        help="snapshot to take Bezirk/Ortsteil boundaries from (default: newest in --snapshot-dir)")
    parser.add_argument("--minzoom", type=int, default=9)
    parser.add_argument("--maxzoom", type=int, default=14)
    args = parser.parse_args()

    versions_path = DATA / f"{args.slug}_versions.parquet"
    timeline_path = SITE / "data" / f"{args.slug}_timeline.json"
    tiles_path = SITE / "tiles" / f"{args.slug}.pmtiles"
    areas_json = SITE / "data" / f"{args.slug}_areas.json"
    areas_geojson = SITE / "data" / f"{args.slug}_areas.geojson"
    spans_path = SITE / "data" / f"{args.slug}_spans.bin"

    started = time.time()
    if args.command == "update":
        _update(args, versions_path, timeline_path, spans_path, areas_json, areas_geojson, tiles_path)
        print(f"done in {time.time() - started:.0f} s", flush=True)
        return

    if args.command in ("all", "snapshots", "classify"):
        snapshot_list = _snapshot_list(args)
        print(f"[snapshots] {len(snapshot_list)}: {snapshot_list[0][0]} … {snapshot_list[-1][0]}", flush=True)
        if args.command == "snapshots":
            return
        print("[classify]", flush=True)
        classified = classify_all(snapshot_list, args.classified_dir, args.threads, args.force)
        if args.command == "classify":
            return
    else:
        classified = None

    if args.command in ("all", "versions"):
        print("[versions]", flush=True)
        build_versions(classified or _existing_classified(args.classified_dir),
                       versions_path, timeline_path, region=args.region)
        write_spans(versions_path, timeline_path, spans_path)
        if args.command == "versions":
            return

    if args.command in ("all", "areas"):
        print("[areas]", flush=True)
        boundaries = args.boundaries_pbf or max(
            Path(args.snapshot_dir).glob("*.osm.pbf"), key=snaps.date_from_filename)
        build_areas(boundaries, versions_path, timeline_path, areas_json, areas_geojson)
        if args.command == "areas":
            return

    print("[tiles]", flush=True)
    build_tiles(versions_path, tiles_path, args.minzoom, args.maxzoom)
    print(f"done in {time.time() - started:.0f} s", flush=True)


def _github_output(**values):
    """Hand results to the next steps of a GitHub workflow, if there is one."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")


def _update(args, versions_path, timeline_path, spans_path, areas_json, areas_geojson, tiles_path):
    """One newer extract onto the existing versions: classify, append, then the
    areas (with the extract's boundaries) and the tiles from scratch."""
    files = [p for pattern in args.pbf for p in sorted(glob.glob(pattern))]
    if len(files) != 1:
        raise SystemExit("update: pass the one new extract with --pbf")
    pbf = Path(files[0])
    snapshot_date = snaps.parse_date(args.date) if args.date else snaps.extract_date(pbf)
    last = date.fromisoformat(json.loads(timeline_path.read_text(encoding="utf-8"))["snapshots"][-1]["date"])
    if snapshot_date <= last:
        print(f"[update] {pbf.name} is from {snapshot_date}, not after the last snapshot {last}: nothing to do",
              flush=True)
        _github_output(changed="false", date=snapshot_date.isoformat())
        return
    print(f"[update] {pbf.name} from {snapshot_date}, after {last}", flush=True)
    snaps.check_coverage(pbf)

    print("[classify]", flush=True)
    classified = classify_snapshot(snapshot_date, pbf, args.classified_dir / f"{snapshot_date.isoformat()}.parquet",
                                   args.threads)
    print("[versions]", flush=True)
    append_snapshot(versions_path, timeline_path, classified, snapshot_date)
    write_spans(versions_path, timeline_path, spans_path)
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
    current = json.loads(areas_json.read_text(encoding="utf-8")) if areas_json.is_file() else {"areas": []}
    build_areas(pbf, versions_path, timeline_path, areas_json, areas_geojson,
                keep=[a["slug"] for a in current["areas"]])
    print("[tiles]", flush=True)
    build_tiles(versions_path, tiles_path, args.minzoom, args.maxzoom)
    _github_output(changed="true", date=snapshot_date.isoformat())


if __name__ == "__main__":
    main()
