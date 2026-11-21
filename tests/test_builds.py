import json

import pytest

from pipeline import builds
from pipeline.builds import REQUIRED, Region, build_id, stale_builds


def make_work(region, **contents):
    """A work directory with every required file, `contents` overriding some."""
    work = region.start()
    for name in REQUIRED:
        data = contents.get(name, name)
        if name == "timeline.json":
            data = json.dumps(data if isinstance(data, dict) else {"snapshots": []})
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(data, encoding="utf-8")
    return work


def test_id_is_content(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        (d / "x.bin").write_bytes(b"one")
    assert build_id(a) == build_id(b)
    (b / "x.bin").write_bytes(b"two")
    assert build_id(a) != build_id(b)
    # A renamed file is another build too.
    (b / "x.bin").rename(b / "y.bin")
    (b / "y.bin").write_bytes(b"one")
    assert build_id(a) != build_id(b)


def test_finish_points_the_timeline_at_the_build(tmp_path):
    region = Region(tmp_path / "berlin")
    make_work(region, **{"timeline.json": {"snapshots": [], "build": "old"}})
    build = region.finish()

    assert not region.work.exists()
    assert region.current() == build
    directory = region.build_dir(build)
    assert build_id(directory) == build
    # The build's own timeline does not name it; the one on top does, with its files.
    assert "build" not in json.loads((directory / "timeline.json").read_text())
    pointer = json.loads(region.pointer.read_text())
    assert pointer["build"] == build
    assert pointer["files"] == sorted(REQUIRED)
    assert region.check() == build


def test_a_changed_area_is_a_new_build(tmp_path):
    """The case the versions hash missed: same versions, other areas."""
    region = Region(tmp_path / "berlin")
    make_work(region)
    first = region.finish()
    keep = [n for n in REQUIRED if not n.startswith(("areas", "outline"))]
    work = region.start(keep=keep)
    assert builds.files_of(work) == sorted(keep)
    (work / "areas").mkdir()
    (work / "areas/index.json").write_text("other", encoding="utf-8")
    for name in ("areas.pmtiles", "outline.geojson"):
        (work / name).write_text(name, encoding="utf-8")
    second = region.finish()

    assert second != first
    assert (region.build_dir(first) / "areas/index.json").read_text() == "areas/index.json"
    assert (region.build_dir(second) / "areas/index.json").read_text() == "other"


def test_a_folder_is_taken_over_whole(tmp_path):
    """`tiles` alone keeps every area bundle, not just the ones REQUIRED names."""
    region = Region(tmp_path / "germany")
    work = make_work(region)
    (work / "areas/kreis-a.json").write_text("{}", encoding="utf-8")
    region.finish()
    work = region.start(keep=["areas/", "timeline.json"])
    assert builds.files_of(work) == ["areas/index.json", "areas/kreis-a.json", "timeline.json"]


def test_the_same_bytes_are_the_same_build(tmp_path):
    region = Region(tmp_path / "berlin")
    make_work(region)
    first = region.finish()
    make_work(region)
    assert region.finish() == first


def test_old_local_builds_go(tmp_path):
    region = Region(tmp_path / "berlin")
    ids = []
    for i in range(3):
        make_work(region, **{"spans.bin": str(i)})
        ids.append(region.finish())
    kept = sorted(p.name for p in (region.dir / "builds").iterdir())
    assert kept == sorted(ids[1:])


def test_a_failed_run_leaves_the_build(tmp_path):
    region = Region(tmp_path / "berlin")
    make_work(region)
    build = region.finish()
    region.start(keep=REQUIRED)
    (region.work / "spans.bin").write_text("half", encoding="utf-8")
    # No finish: the run broke off.
    assert region.current() == build
    assert region.check() == build


def test_check_finds_a_changed_or_missing_file(tmp_path):
    region = Region(tmp_path / "berlin")
    make_work(region)
    build = region.finish()
    (region.build_dir(build) / "spans.bin").write_text("changed", encoding="utf-8")
    with pytest.raises(SystemExit, match="changed after the build"):
        region.check()
    (region.build_dir(build) / "spans.bin").unlink()
    with pytest.raises(SystemExit, match="incomplete"):
        region.check()


def test_continuing_needs_a_build(tmp_path):
    with pytest.raises(SystemExit, match="no build to continue from"):
        Region(tmp_path / "berlin").start(keep=["versions.parquet"])


def test_fetch(tmp_path, monkeypatch):
    """A published build, downloaded: the same build, checked by its id."""
    published = Region(tmp_path / "bucket" / "berlin")
    make_work(published)
    build = published.finish()
    base = "https://example.org/berlin/"

    def fetch_file(url):
        assert url.startswith(base)
        return (published.dir / url[len(base):]).read_bytes()

    monkeypatch.setattr(builds, "_get", fetch_file)
    monkeypatch.setattr(builds, "_download", lambda url, path: path.write_bytes(fetch_file(url)))
    local = Region(tmp_path / "site" / "berlin")
    assert local.fetch(base) == build
    assert local.check() == build
    assert local.pointer.read_bytes() == published.pointer.read_bytes()

    # A file that arrives changed stops it.
    (published.build_dir(build) / "tiles.pmtiles").write_text("corrupt", encoding="utf-8")
    other = Region(tmp_path / "other" / "berlin")
    with pytest.raises(SystemExit, match="incomplete or changed"):
        other.fetch(base)
    assert other.current() is None


def test_stale_builds():
    prefix = "berlin/builds/"
    listing = [
        {"fileName": f"{prefix}{'a' * 12}/tiles.pmtiles", "uploadTimestamp": 1},
        {"fileName": f"{prefix}{'b' * 12}/tiles.pmtiles", "uploadTimestamp": 2},
        {"fileName": f"{prefix}{'c' * 12}/tiles.pmtiles", "uploadTimestamp": 3},
        {"fileName": f"{prefix}{'c' * 12}/spans.bin", "uploadTimestamp": 4},
        {"fileName": "berlin/timeline.json", "uploadTimestamp": 5},
        {"fileName": f"{prefix}not-a-build/x", "uploadTimestamp": 6},
    ]
    # The newest is current: the one before it stays for pages loaded just before.
    assert stale_builds(listing, prefix, "c" * 12) == ["a" * 12]
    # Rolled back to an older build: it stays, and the newest other.
    assert stale_builds(listing, prefix, "a" * 12) == ["b" * 12]
    assert stale_builds(listing, prefix, "c" * 12, keep=1) == ["b" * 12, "a" * 12]
    assert stale_builds(listing, prefix, "c" * 12, keep=3) == []


def test_read_listing():
    entry = {"fileName": "x", "uploadTimestamp": 1}
    assert builds._read_listing(json.dumps([entry])) == [entry]
    assert builds._read_listing(json.dumps(entry) + "\n" + json.dumps(entry)) == [entry, entry]
    assert builds._read_listing("") == []
