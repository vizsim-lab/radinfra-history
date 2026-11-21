"""Builds: one complete set of a region's data, in a directory of its own.

    site/data/<slug>/timeline.json          points at the current build
    site/data/<slug>/builds/<id>/           one build, never changed once made
        timeline.json  versions.parquet  spans.bin  areas.json  areas.geojson
        tiles.pmtiles  tiles_lo.pmtiles

The bucket has the same layout (scripts/publish_b2.sh). A build's id is a hash of
every file in it, so a path always means the same bytes and can be cached for
good, and a rebuild that changes anything, even only the areas, gets a new path.
The timeline at the top is the one file that changes: the build's own timeline
plus `build` and `files`, which is how the page and fetch_data find the rest. The
versions file is in the build too, as the state the next update continues from;
the page never loads it.

A pipeline command works in <slug>/work/, starting from the files of the current
build it does not make anew. `finish` turns that into a build and points the
timeline at it, so a run that fails leaves the current build as it was.

    python -m pipeline.builds finish site/data/berlin       # work/ → a build
    python -m pipeline.builds check site/data/berlin        # prints the current id
    python -m pipeline.builds fetch site/data/berlin URL    # a published build
    b2 ls --json -r b2://bucket/berlin/builds/ | python -m pipeline.builds stale berlin/builds/ ID 2
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

# What a build has before it can be published.
REQUIRED = ("timeline.json", "versions.parquet", "spans.bin", "areas.json", "areas.geojson",
            "tiles.pmtiles", "tiles_lo.pmtiles")
ID = re.compile(r"[0-9a-f]{12}$")


def files_of(directory):
    """Every file in a directory, as sorted relative paths."""
    directory = Path(directory)
    return sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file())


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_id(directory):
    """The first 12 hex digits of a hash over every file's name and content."""
    digest = hashlib.sha256()
    for name in files_of(directory):
        digest.update(f"{name}\0{_sha256(Path(directory) / name)}\n".encode())
    return digest.hexdigest()[:12]


def _write_json(path, data):
    """Written next to the target and moved over it: never half a file."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


class Region:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.pointer = self.dir / "timeline.json"
        self.work = self.dir / "work"

    def build_dir(self, build):
        return self.dir / "builds" / build

    def current(self):
        """Id of the current build, or None."""
        if not self.pointer.is_file():
            return None
        return json.loads(self.pointer.read_text(encoding="utf-8")).get("build")

    def start(self, keep=()):
        """An empty work directory, with copies of the files `keep` of the current
        build where it has them. Copies, not links: the steps rewrite files in place."""
        if self.work.exists():
            shutil.rmtree(self.work)
        self.work.mkdir(parents=True)
        if keep:
            build = self.current()
            if build is None:
                raise SystemExit(f"{self.dir}: no build to continue from "
                                 "(scripts/fetch_data.sh, or the pipeline's `all` first)")
            for name in keep:
                source = self.build_dir(build) / name
                if source.is_file():
                    shutil.copy2(source, self.work / name)
        return self.work

    def finish(self):
        """Make the work directory a build, point the timeline at it, return its id.
        Keeps the build that was current before, deletes older local ones."""
        timeline = json.loads((self.work / "timeline.json").read_text(encoding="utf-8"))
        # The path names the build; its own timeline does not repeat it.
        if "build" in timeline or "files" in timeline:
            timeline.pop("build", None)
            timeline.pop("files", None)
            _write_json(self.work / "timeline.json", timeline)
        build = build_id(self.work)
        target = self.build_dir(build)
        if target.is_dir() and build_id(target) == build:
            shutil.rmtree(self.work)  # the same bytes are a build already
        else:
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            self.work.rename(target)
        previous = self.current()
        _write_json(self.pointer, {**timeline, "build": build, "files": files_of(target)})
        self._prune({build, previous})
        missing = [name for name in REQUIRED if not (target / name).is_file()]
        print(f"[build] {build}: {target}" + (f" (incomplete: no {', '.join(missing)})" if missing else ""),
              flush=True)
        return build

    def _prune(self, keep):
        builds = self.dir / "builds"
        for path in builds.iterdir() if builds.is_dir() else ():
            if path.is_dir() and ID.match(path.name) and path.name not in keep:
                shutil.rmtree(path)

    def check(self):
        """The current build, complete and unchanged since it was made: its id."""
        build = self.current()
        if build is None:
            raise SystemExit(f"{self.dir}: no current build")
        directory = self.build_dir(build)
        if not directory.is_dir():
            raise SystemExit(f"{self.dir}: the timeline names build {build}, which is not there")
        missing = [name for name in REQUIRED if not (directory / name).is_file()]
        if missing:
            raise SystemExit(f"build {build} is incomplete: no {', '.join(missing)}")
        actual = build_id(directory)
        if actual != build:
            raise SystemExit(f"build {build}: its files hash to {actual}, so they changed after the build")
        listed = json.loads(self.pointer.read_text(encoding="utf-8")).get("files")
        if listed != files_of(directory):
            raise SystemExit(f"{self.pointer}: `files` does not match build {build}")
        return build

    def fetch(self, base_url):
        """Download the current build of a published region; `base_url` is the
        region's folder, e.g. https://tiles.vizsim.de/file/radinfra-history/berlin/."""
        base_url = base_url.rstrip("/") + "/"
        raw = _get(base_url + "timeline.json")
        pointer = json.loads(raw)
        build = pointer["build"]
        target = self.build_dir(build)
        if target.is_dir() and build_id(target) == build:
            print(f"[fetch] build {build} is here already", flush=True)
        else:
            work = self.start()
            for name in pointer["files"]:
                path = work / name
                path.parent.mkdir(parents=True, exist_ok=True)
                print(f"[fetch] {name}", flush=True)
                _download(f"{base_url}builds/{build}/{name}", path)
            actual = build_id(work)
            if actual != build:
                raise SystemExit(f"downloaded build {build} hashes to {actual}: incomplete or changed, try again")
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            work.rename(target)
        previous = self.current()
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.pointer.with_name(f".{self.pointer.name}.tmp")
        tmp.write_bytes(raw)
        tmp.replace(self.pointer)
        self._prune({build, previous})
        print(f"[fetch] build {build}: {target}", flush=True)
        return build


USER_AGENT = "radinfra-history (scripts/fetch_data.sh)"


def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _download(url, path):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, open(path, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)


def stale_builds(listing, prefix, current, keep=2):
    """Builds in a bucket listing to delete: all but the current one and the most
    recently uploaded others, `keep` builds in all. `listing`: file entries as
    `b2 ls --json` gives them (fileName, uploadTimestamp)."""
    newest = {}
    for entry in listing:
        name = entry["fileName"]
        if not name.startswith(prefix):
            continue
        build, _, rest = name[len(prefix):].partition("/")
        if ID.match(build) and rest:
            newest[build] = max(newest.get(build, 0), entry.get("uploadTimestamp", 0))
    others = sorted((b for b in newest if b != current), key=lambda b: newest[b], reverse=True)
    return others[max(keep - 1, 0):]


def _read_listing(text):
    """`b2 ls --json` output: one JSON array, or one object per line."""
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m pipeline.builds", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("finish", "check"):
        sub.add_parser(name).add_argument("region_dir", type=Path)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("region_dir", type=Path)
    fetch.add_argument("url")
    stale = sub.add_parser("stale", help="reads `b2 ls --json` on stdin, prints the builds to delete")
    stale.add_argument("prefix")
    stale.add_argument("current")
    stale.add_argument("keep", type=int)
    args = parser.parse_args(argv)

    if args.command == "stale":
        for build in stale_builds(_read_listing(sys.stdin.read()), args.prefix, args.current, args.keep):
            print(build)
        return
    region = Region(args.region_dir)
    if args.command == "finish":
        region.finish()
    elif args.command == "check":
        print(region.check())
    else:
        region.fetch(args.url)


if __name__ == "__main__":
    main()
