import json
from datetime import date, datetime, timezone

import pytest

from pipeline import classify, snapshots


@pytest.fixture
def fake_tools(monkeypatch, tmp_path):
    """Cutting and classifying without osmium and OSMnexus; records what ran."""
    calls = {"cut": [], "classify": []}

    def cut(osh, at, path):
        calls["cut"].append(at)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"snapshot at {at}")
        return path

    def classify_one(snapshot_date, pbf, out, threads=1):
        assert pbf.is_file(), f"classified {snapshot_date} without its snapshot"
        calls["classify"].append(snapshot_date)
        out.write_text("classified")
        return out

    monkeypatch.setattr(snapshots, "cut_snapshot", cut)
    monkeypatch.setattr(classify, "classify_snapshot", classify_one)
    return calls


PLAN = [(date(2020, 1, 1), "2020-01-01T00:00:00Z"), (date(2020, 4, 1), "2020-04-01T00:00:00Z"),
        (date(2020, 5, 17), None)]


@pytest.mark.parametrize("jobs", [1, 3])
def test_one_at_a_time_keeps_only_the_newest(fake_tools, tmp_path, jobs):
    snap, out = tmp_path / "snapshots", tmp_path / "classified"
    result = classify.classify_history("h.osh.pbf", PLAN, snap, out, jobs=jobs, keep=[date(2020, 5, 17)])
    assert [d for d, _ in result] == [d for d, _ in PLAN]
    assert sorted(p.name for p in out.iterdir()) == ["2020-01-01.parquet", "2020-04-01.parquet", "2020-05-17.parquet"]
    assert [p.name for p in snap.iterdir()] == ["2020-05-17.osm.pbf"]
    assert sorted(fake_tools["cut"], key=str) == sorted([at for _, at in PLAN], key=str)


def test_newest_first(fake_tools, tmp_path):
    classify.classify_history("h.osh.pbf", PLAN, tmp_path / "s", tmp_path / "c", keep=[date(2020, 5, 17)])
    assert fake_tools["classify"] == [date(2020, 5, 17), date(2020, 4, 1), date(2020, 1, 1)]


def test_carries_on_without_cutting_again(fake_tools, tmp_path):
    snap, out = tmp_path / "snapshots", tmp_path / "classified"
    out.mkdir()
    (out / "2020-01-01.parquet").write_text("from before")
    # An earlier run cut this one, then broke off before classifying it.
    snap.mkdir()
    (snap / "2020-04-01.osm.pbf").write_text("cut before")
    classify.classify_history("h.osh.pbf", PLAN, snap, out, keep=[date(2020, 5, 17)])
    assert fake_tools["cut"] == [None]
    assert fake_tools["classify"] == [date(2020, 5, 17), date(2020, 4, 1)]
    assert (out / "2020-01-01.parquet").read_text() == "from before"


def test_the_kept_snapshot_is_cut_again_if_gone(fake_tools, tmp_path):
    """Everything classified, but the newest snapshot (for the areas) deleted."""
    out = tmp_path / "classified"
    out.mkdir()
    for d, _ in PLAN:
        (out / f"{d}.parquet").write_text("done")
    classify.classify_history("h.osh.pbf", PLAN, tmp_path / "s", out, keep=[date(2020, 5, 17)])
    assert fake_tools["cut"] == [None]
    assert fake_tools["classify"] == []


def test_force_classifies_again(fake_tools, tmp_path):
    out = tmp_path / "classified"
    out.mkdir()
    (out / "2020-01-01.parquet").write_text("old")
    classify.classify_history("h.osh.pbf", PLAN[:1], tmp_path / "s", out, force=True)
    assert (out / "2020-01-01.parquet").read_text() == "classified"


def test_stops_short_of_a_full_disk(fake_tools, tmp_path, monkeypatch):
    monkeypatch.setattr(classify, "MIN_FREE_BYTES", float("inf"))
    with pytest.raises(SystemExit, match="GB free"):
        classify.classify_history("h.osh.pbf", PLAN, tmp_path / "s", tmp_path / "c")
    assert fake_tools["cut"] == []


def test_a_failure_stops_the_run(fake_tools, tmp_path, monkeypatch):
    def broken(snapshot_date, pbf, out, threads=1):
        raise SystemExit("OSMnexus failed")
    monkeypatch.setattr(classify, "classify_snapshot", broken)
    with pytest.raises(SystemExit, match="OSMnexus failed"):
        classify.classify_history("h.osh.pbf", PLAN, tmp_path / "s", tmp_path / "c")
    assert not list((tmp_path / "c").iterdir())


def test_snapshot_plan(monkeypatch):
    end = datetime(2020, 5, 17, 8, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(snapshots, "history_end", lambda osh, cache=None: end)
    assert snapshots.snapshot_plan("h", date(2020, 1, 1), 3) == PLAN
    # An end given: up to it, without the file's own end.
    assert snapshots.snapshot_plan("h", date(2020, 1, 1), 3, end=date(2020, 4, 30)) == PLAN[:2]
    # The file ends on a regular date: no extra snapshot.
    monkeypatch.setattr(snapshots, "history_end",
                        lambda osh, cache=None: datetime(2020, 4, 1, tzinfo=timezone.utc))
    assert snapshots.snapshot_plan("h", date(2020, 1, 1), 3) == PLAN[:2]


def test_history_end_is_cached(monkeypatch, tmp_path):
    osh = tmp_path / "h.osh.pbf"
    osh.write_bytes(b"history")
    cache = tmp_path / "history.json"
    runs = []

    class Result:
        stdout = "2020-05-17T08:30:00Z\n"

    monkeypatch.setattr(snapshots.tools, "osmium", lambda: "osmium")
    monkeypatch.setattr(snapshots.tools, "run", lambda cmd, **kw: runs.append(cmd) or Result())
    first = snapshots.history_end(osh, cache)
    assert snapshots.history_end(osh, cache) == first == datetime(2020, 5, 17, 8, 30, tzinfo=timezone.utc)
    assert len(runs) == 1
    assert json.loads(cache.read_text())["file"] == "h.osh.pbf"
    # Another file (other size): read again.
    osh.write_bytes(b"a longer history")
    snapshots.history_end(osh, cache)
    assert len(runs) == 2
