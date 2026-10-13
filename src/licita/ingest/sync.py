"""Refresh connector: keep the local PLACSP mirror, the spine and the fact sheets current, unattended.

  licita sync                 # closed months only (the default: a month enters once PLACSP has closed it)
  licita sync --current-month # also the month in progress (PLACSP republishes it daily)
  licita sync --dry-run       # show what would be fetched
  licita sync --reparse       # fetch and parse every archive again (parser or profile filter changed)

What it does, in order:
  1. Plans the archives that should exist (yearly up to last year, monthly for this year up to last month).
  2. Asks the server for each archive's ETag (one streaming GET closed after the headers; HEAD is reset).
  3. Fetches and parses only the archives whose ETag changed or whose Parquet is missing; writes Parquet
     atomically; records ETag, size, counts and the parse filter in data/state/archives.json.
  4. If anything changed (or there is no live spine), builds a new spine. The build runs the quality gate
     (licita.spine.dq) before swapping the symlink, so a bad archive never reaches readers.
  5. With --facts, regenerates the fact sheets of the profile's buyers.
A lock file makes concurrent runs harmless; every run appends one line to data/state/sync_log.jsonl.
Schedule it with `make schedule-install` (launchd) or the cron line in docs/deploy/DEPLOYMENT.md.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from licita.config import PARQUET_DIR, RAW_DIR, SPINES_DIR, STATE_DIR
from licita.ingest.backfill import plan
from licita.ingest.placsp_http import Archive, ArchiveCorrupt, ArchiveMissing, client, download, probe_etag
from licita.ingest.placsp_parse import PARSER_VERSION, parse_archive
from licita.profile import load_profile

MANIFEST = STATE_DIR / "archives.json"
LEGACY_SIZES = STATE_DIR / "archive_sizes.json"
LOG = STATE_DIR / "sync_log.jsonl"
LOCK = STATE_DIR / "sync.lock"


@dataclass
class Decision:
    archive: str
    action: str          # fetch | skip | missing
    reason: str


@dataclass
class SyncResult:
    started: str
    decisions: list[Decision] = field(default_factory=list)
    fetched: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    built: str | None = None
    facts: list[str] = field(default_factory=list)
    seconds: float = 0.0
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.failed


def parse_signature() -> str:
    """Hash of everything that decides which rows the parser keeps: a change means every archive must be re-parsed."""
    r = load_profile().region
    raw = json.dumps({"parser": PARSER_VERSION, "chain": r.buyer_chain_re, "terms": sorted(r.health_terms)}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    # first run after the original backfill: adopt the recorded sizes so nothing is downloaded twice
    out = {}
    if LEGACY_SIZES.exists():
        for name, size in json.loads(LEGACY_SIZES.read_text()).items():
            if size and size > 0 and (PARQUET_DIR / f"p{name}_folders.parquet").exists():
                out[name] = {"size": size, "etag": None, "fetched_at": None, "signature": None}
    return out


def save_manifest(m: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True))
    tmp.replace(MANIFEST)


def decide(archives: list[Archive], server: dict[str, tuple[str | None, int | None] | None], manifest: dict,
           signature: str, reparse: bool = False, parquet_dir: Path = PARQUET_DIR) -> list[Decision]:
    """Pure: which archives to fetch given server ETags, the manifest and the current parse signature."""
    out = []
    for a in archives:
        srv = server.get(a.name)
        have = manifest.get(a.name)
        parquet = (parquet_dir / f"p{a.name}_folders.parquet").exists()
        if srv is None:
            out.append(Decision(a.name, "missing", "not on server"))
        elif reparse:
            out.append(Decision(a.name, "fetch", "reparse requested"))
        elif not parquet or have is None:
            out.append(Decision(a.name, "fetch", "no local copy"))
        elif have.get("signature") not in (None, signature):
            out.append(Decision(a.name, "fetch", "parse filter changed"))
        elif have.get("etag") and srv[0] and have["etag"] != srv[0]:
            out.append(Decision(a.name, "fetch", f"etag {have['etag']} -> {srv[0]}"))
        elif have.get("etag") is None and srv[1] is not None and have.get("size") != srv[1]:
            out.append(Decision(a.name, "fetch", f"size {have.get('size')} -> {srv[1]}"))
        else:
            out.append(Decision(a.name, "skip", "unchanged"))
    return out


def probe_all(archives: list[Archive], workers: int = 8) -> dict[str, tuple[str | None, int | None] | None]:
    def one(a: Archive):
        with client() as c:
            try:
                return a.name, probe_etag(c, a)
            except ArchiveMissing:
                return a.name, None
    with ThreadPoolExecutor(workers) as ex:
        return dict(ex.map(one, archives))


def fetch_one(a: Archive, signature: str) -> dict:
    with client() as c:
        etag, size = probe_etag(c, a)
        z = download(c, a, RAW_DIR)
    counts = parse_archive(z, PARQUET_DIR / f"p{a.name}", a.feed)
    z.unlink()
    return {"archive": a.name, "etag": etag, "size": size, "signature": signature,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"), **counts}


def run(include_current: bool = False, dry_run: bool = False, do_build: bool = True, facts: bool = False,
        force_build: bool = False, reparse: bool = False, workers: int = 4, today: date | None = None,
        prober: Callable[[list[Archive]], dict] = probe_all,
        fetcher: Callable[[Archive, str], dict] = fetch_one) -> SyncResult:
    t0 = time.time()
    today = today or date.today()
    res = SyncResult(started=datetime.now(UTC).isoformat(timespec="seconds"))
    archives = plan(today, include_current=include_current)
    signature = parse_signature()
    manifest = load_manifest()
    server = prober(archives)
    res.decisions = decide(archives, server, manifest, signature, reparse=reparse, parquet_dir=PARQUET_DIR)
    todo = [a for a, d in zip(archives, res.decisions, strict=True) if d.action == "fetch"]
    for d in res.decisions:
        if d.action != "skip":
            print(json.dumps(asdict(d)), flush=True)
    print(f"{len(archives)} archives planned, {len(todo)} to fetch, "
          f"{sum(d.action == 'missing' for d in res.decisions)} not on server", flush=True)
    if dry_run:
        res.note = "dry run"
        res.seconds = time.time() - t0
        return res

    with ThreadPoolExecutor(workers) as ex:
        for a, fut in [(a, ex.submit(fetcher, a, signature)) for a in todo]:
            try:
                rec = fut.result()
                manifest[a.name] = {k: rec[k] for k in ("etag", "size", "signature", "fetched_at", "entries", "kept", "results")}
                res.fetched.append(rec)
            except (ArchiveMissing, ArchiveCorrupt, OSError) as e:
                rec = {"archive": a.name, "error": f"{type(e).__name__}: {e}"}
                res.failed.append(rec)
            print(json.dumps(rec), flush=True)
    save_manifest(manifest)

    live = (SPINES_DIR / "spine.duckdb").exists()
    if do_build and not res.failed and (res.fetched or force_build or not live):
        from licita.spine.build import build  # heavy import; only when needed
        out = build(today)
        res.built = out.name
        if facts:
            from licita.memo import facts as facts_mod
            for key in load_profile().buyers:
                facts_mod.build_all(key, ("start_now", "due_6m", "verify"))
                res.facts.append(key)
    elif res.failed:
        res.note = "fetch failures: spine not rebuilt; rerun to resume"
    else:
        res.note = "nothing changed; spine kept"
    res.seconds = round(time.time() - t0, 1)
    return res


def _log(res: SyncResult) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"started": res.started, "seconds": res.seconds, "fetched": [f["archive"] for f in res.fetched],
           "failed": [f["archive"] for f in res.failed], "built": res.built, "facts": res.facts, "note": res.note}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--current-month", action="store_true", help="also fetch the month in progress")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-build", action="store_true", help="fetch only")
    ap.add_argument("--force-build", action="store_true", help="rebuild the spine even if no archive changed")
    ap.add_argument("--facts", action="store_true", help="regenerate fact sheets for the profile's buyers after a build")
    ap.add_argument("--reparse", action="store_true", help="fetch and parse every archive again")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("sync: another run holds the lock; nothing to do", file=sys.stderr)
            return 0
        res = run(include_current=args.current_month, dry_run=args.dry_run, do_build=not args.no_build,
                  facts=args.facts, force_build=args.force_build, reparse=args.reparse, workers=args.workers)
        if not args.dry_run:
            _log(res)
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
