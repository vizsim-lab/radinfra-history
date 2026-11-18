"""radinfra-history pipeline.

    uv run python -m pipeline all --osh berlin-internal.osh.pbf --start 2020-01-01 --step 3
    uv run python -m pipeline all --pbf data/dated/berlin-2*.osm.pbf

Steps, each also runnable on its own: snapshots -> classify -> versions -> tiles.
"""

import argparse
import glob
import time
from pathlib import Path

from . import snapshots as snaps
from .classify import classify_all
from .tiles import build_tiles
from .versions import build_versions

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
    parser.add_argument("command", choices=["all", "snapshots", "classify", "versions", "tiles"])
    source = parser.add_argument_group("input (snapshots, classify, all)")
    source.add_argument("--osh", help="full-history .osh.pbf to cut snapshots from")
    source.add_argument("--start", default="2020-01-01", help="first snapshot date (default 2020-01-01)")
    source.add_argument("--step", type=int, default=3, help="months between snapshots (default 3)")
    source.add_argument("--end", help="last regular snapshot date (default: end of the history file)")
    source.add_argument("--pbf", nargs="*", default=[], help="dated extracts instead of --osh (globs ok)")
    parser.add_argument("--region", default="Berlin")
    parser.add_argument("--slug", default="berlin", help="file name stem for tiles and timeline")
    parser.add_argument("--snapshot-dir", default=DATA / "snapshots", type=Path)
    parser.add_argument("--classified-dir", default=DATA / "classified", type=Path)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--force", action="store_true", help="reclassify even if results exist")
    parser.add_argument("--minzoom", type=int, default=9)
    parser.add_argument("--maxzoom", type=int, default=14)
    args = parser.parse_args()

    versions_path = DATA / f"{args.slug}_versions.parquet"
    timeline_path = SITE / "data" / f"{args.slug}_timeline.json"
    tiles_path = SITE / "tiles" / f"{args.slug}.pmtiles"

    started = time.time()
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
        if args.command == "versions":
            return

    print("[tiles]", flush=True)
    build_tiles(versions_path, tiles_path, args.minzoom, args.maxzoom)
    print(f"done in {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
