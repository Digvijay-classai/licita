"""Download → parse → delete, in parallel, resumable. Progress via `python -m licita.ingest.status`.

Usage:
  python -m licita.ingest.backfill                 # full plan (643 2017–2025 yearly + 2026 monthly, 1044 same)
  python -m licita.ingest.backfill --only 643_2019 # single archive
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from licita.config import PARQUET_DIR, RAW_DIR, STATE_DIR
from licita.ingest.placsp_http import Archive, ArchiveCorrupt, ArchiveMissing, client, download, probe_size
from licita.ingest.placsp_parse import parse_archive

FIRST_YEAR = 2017


def plan(today: date | None = None, include_current: bool = False) -> list[Archive]:
    """Yearly archives up to last year, monthly archives for the current year up to last month, and with
    `include_current` the current month's archive (PLACSP republishes it as the month progresses)."""
    today = today or date.today()
    last = today.month + (1 if include_current else 0)
    out = []
    for feed in ("643", "1044"):
        out += [Archive(feed, str(y)) for y in range(FIRST_YEAR, today.year)]
        out += [Archive(feed, f"{today.year}{m:02d}") for m in range(1, last)]
    return out


def current_month(today: date | None = None) -> set[str]:
    today = today or date.today()
    return {f"{feed}_{today.year}{today.month:02d}" for feed in ("643", "1044")}


def _done(a: Archive) -> bool:
    return (PARQUET_DIR / f"p{a.name}_folders.parquet").exists()


def _sizes_file() -> Path:
    return STATE_DIR / "archive_sizes.json"


def record_sizes(archives: list[Archive]) -> None:
    sizes = json.loads(_sizes_file().read_text()) if _sizes_file().exists() else {}
    missing = [a for a in archives if a.name not in sizes]

    def probe(a: Archive):
        with client() as c:
            try:
                return a.name, probe_size(c, a)
            except ArchiveMissing:
                return a.name, -1

    with ThreadPoolExecutor(8) as ex:
        for name, size in ex.map(probe, missing):
            sizes[name] = size
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _sizes_file().write_text(json.dumps(sizes, indent=1, sort_keys=True))


def run_one(a: Archive) -> dict:
    with client() as c:
        z = download(c, a, RAW_DIR)
    counts = parse_archive(z, PARQUET_DIR / f"p{a.name}", a.feed)
    z.unlink()
    return {"archive": a.name, **counts}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="archive names like 643_2019")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--current-month", action="store_true",
                    help="also fetch the current month's archive, re-downloading it even if fetched before")
    args = ap.parse_args(argv)

    archives = plan(include_current=args.current_month)
    if args.only:
        archives = [a for a in archives if a.name in set(args.only)]
    fresh = current_month() if args.current_month else set()
    record_sizes([a for a in archives if a.name not in fresh])   # current month's size changes daily
    todo = [a for a in archives if a.name in fresh or not _done(a)]
    print(f"{len(archives)} archives planned, {len(todo)} to fetch", flush=True)
    failures = []
    log = STATE_DIR / "backfill_log.jsonl"
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(run_one, a): a for a in todo}
        for fut in as_completed(futs):
            a = futs[fut]
            try:
                rec = fut.result()
            except ArchiveMissing as e:
                rec = {"archive": a.name, "error": f"ArchiveMissing: {e}"}
                if a.name in fresh:
                    rec["note"] = "current month not published yet; the spine stays at the last full month"
                else:
                    failures.append(rec)
            except (ArchiveCorrupt, OSError) as e:
                rec = {"archive": a.name, "error": f"{type(e).__name__}: {e}"}
                failures.append(rec)
            with open(log, "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(json.dumps(rec), flush=True)
    if failures:
        print(f"{len(failures)} archive(s) failed; rerun to resume.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
