"""The refresh connector without a network: planning, change detection and a dry run through `run()`."""
import json
from datetime import date

from licita.ingest import sync
from licita.ingest.backfill import plan
from licita.ingest.placsp_http import Archive


def test_plan_covers_closed_months_only():
    names = [a.name for a in plan(date(2026, 10, 10))]
    assert names[:2] == ["643_2017", "643_2018"] and "643_2025" in names
    assert "643_202609" in names and "643_202610" not in names
    assert "1044_202609" in names and "1044_202610" not in names
    assert len(names) == 2 * (9 + 9)                       # 2017–2025 yearly + Jan–Sep monthly, two feeds


def test_decide_fetches_only_what_changed(tmp_path):
    archives = [Archive("643", "2024"), Archive("643", "202609"), Archive("643", "202610"), Archive("1044", "2024")]
    for name in ("643_2024", "643_202609"):
        (tmp_path / f"p{name}_folders.parquet").write_bytes(b"")
    server = {"643_2024": ("aaa-1", 10), "643_202609": ("bbb-2", 20), "643_202610": None, "1044_2024": ("ccc-3", 30)}
    manifest = {"643_2024": {"etag": "aaa-1", "size": 10, "signature": "sig"},
                "643_202609": {"etag": "old-9", "size": 19, "signature": "sig"}}
    d = {x.archive: (x.action, x.reason) for x in sync.decide(archives, server, manifest, "sig", parquet_dir=tmp_path)}
    assert d["643_2024"] == ("skip", "unchanged")
    assert d["643_202609"][0] == "fetch" and "etag" in d["643_202609"][1]
    assert d["643_202610"] == ("missing", "not on server")
    assert d["1044_2024"] == ("fetch", "no local copy")


def test_decide_legacy_manifest_compares_sizes_and_signature_change_reparses(tmp_path):
    a = [Archive("643", "2024")]
    (tmp_path / "p643_2024_folders.parquet").write_bytes(b"")
    legacy = {"643_2024": {"etag": None, "size": 10, "signature": None}}
    assert sync.decide(a, {"643_2024": ("x-1", 10)}, legacy, "sig", parquet_dir=tmp_path)[0].action == "skip"
    assert sync.decide(a, {"643_2024": ("x-1", 11)}, legacy, "sig", parquet_dir=tmp_path)[0].action == "fetch"
    changed = {"643_2024": {"etag": "x-1", "size": 10, "signature": "other"}}
    assert sync.decide(a, {"643_2024": ("x-1", 10)}, changed, "sig", parquet_dir=tmp_path)[0].reason == "parse filter changed"
    assert sync.decide(a, {"643_2024": ("x-1", 10)}, legacy, "sig", reparse=True, parquet_dir=tmp_path)[0].action == "fetch"


def test_run_fetches_and_records_without_building(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "PARQUET_DIR", tmp_path / "parquet")
    monkeypatch.setattr(sync, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(sync, "SPINES_DIR", tmp_path / "spines")
    monkeypatch.setattr(sync, "MANIFEST", tmp_path / "state" / "archives.json")
    monkeypatch.setattr(sync, "LEGACY_SIZES", tmp_path / "state" / "archive_sizes.json")
    (tmp_path / "parquet").mkdir()
    (tmp_path / "state").mkdir()
    fetched = []

    def prober(archives):
        return {a.name: (f"etag-{a.name}", 100) for a in archives}

    def fetcher(a, signature):
        fetched.append(a.name)
        (tmp_path / "parquet" / f"p{a.name}_folders.parquet").write_bytes(b"")
        return {"archive": a.name, "etag": f"etag-{a.name}", "size": 100, "signature": signature,
                "fetched_at": "t", "entries": 1, "kept": 1, "results": 1}

    res = sync.run(do_build=False, today=date(2026, 10, 10), prober=prober, fetcher=fetcher)
    assert res.ok and len(fetched) == 36 and res.built is None
    manifest = json.loads((tmp_path / "state" / "archives.json").read_text())
    assert manifest["643_202609"]["etag"] == "etag-643_202609"
    # second run: nothing changed, nothing fetched
    fetched.clear()
    res = sync.run(do_build=False, today=date(2026, 10, 10), prober=prober, fetcher=fetcher)
    assert fetched == [] and res.note.startswith("nothing changed")
    # a republished archive is fetched again
    res = sync.run(do_build=False, today=date(2026, 10, 10), fetcher=fetcher,
                   prober=lambda ar: {a.name: (("new", 101) if a.name == "643_202608" else (f"etag-{a.name}", 100)) for a in ar})
    assert fetched == ["643_202608"]


def test_dry_run_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "PARQUET_DIR", tmp_path / "parquet")
    monkeypatch.setattr(sync, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(sync, "MANIFEST", tmp_path / "state" / "archives.json")
    monkeypatch.setattr(sync, "LEGACY_SIZES", tmp_path / "state" / "archive_sizes.json")
    res = sync.run(dry_run=True, today=date(2026, 10, 10), prober=lambda ar: {a.name: ("e", 1) for a in ar},
                   fetcher=lambda a, s: (_ for _ in ()).throw(AssertionError("must not fetch")))
    assert res.note == "dry run" and not (tmp_path / "state" / "archives.json").exists()
    assert sum(d.action == "fetch" for d in res.decisions) == 36
