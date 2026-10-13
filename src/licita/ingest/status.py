"""Backfill progress bars: `uv run python -m licita.ingest.status [-w]`."""
from __future__ import annotations

import json
import sys
import time

from licita.config import PARQUET_DIR, RAW_DIR, STATE_DIR

BAR = 28


def _bar(done: int, total: int, width: int = BAR) -> str:
    n = 0 if total <= 0 else min(width, done * width // total)
    return "█" * n + "░" * (width - n)


def _hum(b: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024 or u == "GB":
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} GB"


def draw(prev: tuple[float, int] | None) -> tuple[float, int]:
    sizes = json.loads((STATE_DIR / "archive_sizes.json").read_text())
    log = {}
    lp = STATE_DIR / "backfill_log.jsonl"
    if lp.exists():
        for line in lp.read_text().splitlines():
            rec = json.loads(line)
            log[rec["archive"]] = rec
    got = tot = ndone = 0
    lines = []
    for name, size in sorted(sizes.items(), key=lambda kv: (kv[0].split("_")[0], kv[0])):
        if size is None or size < 0:
            lines.append(f"  {name:12} {'':{BAR}}        not on server")
            continue
        tot += size
        if (PARQUET_DIR / f"p{name}_folders.parquet").exists():
            cur, st = size, f"✅ {log.get(name, {}).get('kept', '?')} rows"
            ndone += 1
        elif (RAW_DIR / f"{name}.zip").exists():
            cur, st = size, "⚙️  parsing"
        elif (RAW_DIR / f"{name}.zip.part").exists():
            cur, st = (RAW_DIR / f"{name}.zip.part").stat().st_size, "⬇️  downloading"
        else:
            cur, st = 0, "⏳ queued"
        if name in log and "error" in log[name]:
            st = "❌ " + log[name]["error"][:40]
        got += cur
        lines.append(f"  {name:12} {_bar(cur, size)} {100 * cur // max(size, 1):3d}%  {_hum(cur):>9} / {_hum(size):<9} {st}")
    now = time.time()
    speed = (got - prev[1]) / (now - prev[0]) if prev and now > prev[0] else 0
    eta = f"{int((tot - got) / speed // 60)}m" if speed > 0 else "—"
    print("\n  LICITA · PLACSP backfill\n")
    print("\n".join(lines))
    print(f"\n  TOTAL        {_bar(got, tot, 40)} {100 * got // max(tot, 1)}%  {_hum(got)} / {_hum(tot)}")
    print(f"               {ndone}/{sum(1 for s in sizes.values() if s and s > 0)} done · {_hum(max(speed, 0))}/s · ETA {eta}")
    return now, got


if __name__ == "__main__":
    prev = None
    if "-w" in sys.argv:
        while True:
            print("\033[2J\033[H", end="")
            prev = draw(prev)
            time.sleep(5)
    else:
        prev = draw(None)
        time.sleep(3)
        print("\033[2J\033[H", end="")
        draw(prev)
