"""Data-quality gate for a built spine: what must hold before it goes live, and what to watch.

  python -m licita.spine.dq                 # report on the live spine
  python -m licita.spine.dq --spine FILE    # report on a given spine

Each check is a row in runs/<run_id>/dq_report.md. `hard` checks block the swap (build.py raises DQError and
deletes the file); `soft` checks are reported. Every threshold below was set from the 2017–2026 archives on
2026-10-10 and is explained in docs/engineering/DATA.md. Lower a threshold only with a note in that table.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb

from licita.config import SPINES_DIR

NIF_RE = r"^[A-Z0-9]\d{7}[A-Z0-9]$"
MIN_MONTHS_FOR_CONTINUITY = 24   # synthetic or partial datasets are not held to the calendar


class DQError(Exception):
    """A hard check failed; the spine must not be swapped in."""


@dataclass(frozen=True)
class Check:
    name: str
    level: str          # hard | soft
    passed: bool
    value: str
    rule: str

    @property
    def status(self) -> str:
        return "pass" if self.passed else ("FAIL" if self.level == "hard" else "warn")


def _one(con, sql, *params):
    return con.execute(sql, list(params)).fetchone()[0]


def last_closed_month_end(as_of: date) -> date:
    return as_of.replace(day=1) - timedelta(days=1)


def continuity(months: list[tuple[str, str, int]], last_month: str | None = None) -> tuple[list[str], list[str], bool]:
    """(empty months, months below 20 % of the trailing six-month median, whether any feed spans enough
    months to be judged) from (feed, 'YYYY-MM', count) rows. Feeds shorter than two years are not judged;
    months after `last_month` (the last closed one) are not either, since they are partial by design."""
    by_feed: dict[str, list[tuple[str, int]]] = {}
    for feed, ym, n in sorted(months):
        if last_month and ym > last_month:
            continue
        by_feed.setdefault(feed, []).append((ym, n))
    gaps, dips = [], []
    for feed, series in by_feed.items():
        if len(series) < MIN_MONTHS_FOR_CONTINUITY:
            continue
        have = dict(series)
        y, m = map(int, series[0][0].split("-"))
        last = series[-1][0]
        cur = f"{y:04d}-{m:02d}"
        hist: list[int] = []
        while cur <= last:
            n = have.get(cur, 0)
            if n == 0:
                gaps.append(f"{feed} {cur}")
            elif len(hist) >= 6 and n < 0.2 * sorted(hist[-6:])[3]:
                dips.append(f"{feed} {cur} ({n:,})")
            hist.append(n)
            m += 1
            if m == 13:
                y, m = y + 1, 1
            cur = f"{y:04d}-{m:02d}"
    span = any(len(s) >= MIN_MONTHS_FOR_CONTINUITY for s in by_feed.values())
    return gaps, dips, span


def run(con: duckdb.DuckDBPyConnection, as_of: date, demo: bool | None = None,
        previous: Path | None = None) -> list[Check]:
    demo = (os.environ.get("LICITA_DEMO") == "1") if demo is None else demo
    out: list[Check] = []

    def add(name, level, passed, value, rule):
        out.append(Check(name, level, bool(passed), str(value), rule))

    # --- structure ------------------------------------------------------------------------------
    n_f = _one(con, "select count(*) from folders")
    n_fk = _one(con, "select count(distinct (buyer_key, folder_id)) from folders")
    add("folders_unique", "hard", n_f == n_fk, f"{n_f:,} rows, {n_fk:,} keys", "one row per (buyer_key, folder_id)")

    n_r = _one(con, "select count(*) from results")
    n_rd = _one(con, "select count(*) from (select distinct * from results)")
    add("results_no_exact_duplicates", "hard", n_r == n_rd, f"{n_r:,} rows, {n_r - n_rd:,} repeats",
        "no award row repeated verbatim")

    cols = {c[0] for c in con.execute("select column_name from information_schema.columns where table_name='results'").fetchall()}
    if "from_latest_snapshot" in cols:
        latest = _one(con, "select count(*) from results where from_latest_snapshot and result_code in ('2','8','9','11')")
        total = _one(con, "select count(*) from results where result_code in ('2','8','9','11')")
        add("awards_retained", "hard", total >= latest, f"{total:,} awards, {total - latest:,} recovered from earlier snapshots",
            "the union over snapshots never holds fewer awards than the latest snapshot alone")

    near = _one(con, """select count(*) from (select 1 from results where result_code in ('2','8','9','11')
        group by buyer_key, folder_id, coalesce(lot_id::varchar,''), coalesce(upper(winner_nif), winner, ''), coalesce(contract_id::varchar,'')
        having count(*) > 1)""")
    add("award_key_unique", "hard", near == 0, f"{near:,} duplicate award keys",
        "one row per (buyer, folder, lot, winner, contract id)")
    if "from_latest_snapshot" in cols:
        versions = _one(con, """select count(*) from (select 1 from results where result_code in ('2','8','9','11')
            group by buyer_key, folder_id, coalesce(lot_id::varchar,''), coalesce(upper(winner_nif), winner, '')
            having count(distinct updated) > 1)""")
        add("award_single_version", "hard", versions == 0, f"{versions:,} (lot, winner) pairs from two snapshots",
            "an award republished later (formalisation, correction) replaces the earlier row, never joins it")

    # --- identities -----------------------------------------------------------------------------
    bad_nif = _one(con, f"select count(*) from folders where buyer_nif is not null and not regexp_matches(buyer_nif, '{NIF_RE}')")
    add("buyer_nif_format", "soft", bad_nif <= max(5, n_f * 0.001), f"{bad_nif:,} malformed of {n_f:,}",
        "<= 0.1 % of folders carry a NIF that is not 9 characters of the official shape")

    frag = _one(con, """
        with k as (select buyer_key, lower(trim(buyer)) nm, lower(trim(coalesce(buyer_city,''))) city,
                          lower(split_part(coalesce(buyer_parents,''), ' > ', 1)) p1 from folders group by all),
        ident as (select distinct nm, city, p1 from k where buyer_key not like 'NAME:%' and buyer_key not like 'DIR3:%')
        select count(*) from k join ident using (nm, city, p1) where k.buyer_key like 'NAME:%' or k.buyer_key like 'DIR3:%'""")
    add("buyer_keys_resolved", "soft", frag == 0, f"{frag:,} unresolved name/DIR3 keys",
        "no name- or DIR3-keyed buyer shares name, city and parent with an identified one")

    # --- dates ----------------------------------------------------------------------------------
    n_lots = _one(con, "select count(*) from lots")
    no_d = _one(con, "select count(*) from lots where award_d is null")
    add("award_date_present", "hard", no_d <= n_lots * 0.05, f"{no_d:,} of {n_lots:,} lots without a date ({no_d / max(n_lots,1):.1%})",
        "<= 5 % of awarded lots lack award, contract and award-notice dates")
    off = _one(con, "select count(*) from lots where award_d is not null and (award_d < date '2000-01-01' or award_d > ?)",
               as_of + timedelta(days=400))
    add("award_date_plausible", "hard", off <= n_lots * 0.005, f"{off:,} outside 2000..as_of+400d",
        "<= 0.5 % of dated lots fall outside the plausible window")

    # --- coverage and continuity ----------------------------------------------------------------
    months = con.execute("""select source_feed, strftime(updated::timestamp, '%Y-%m') ym, count(*) n
                            from snapshots group by 1, 2 order by 1, 2""").fetchall()
    expected = last_closed_month_end(as_of)
    gaps, dips, span = continuity(months, expected.strftime("%Y-%m"))
    add("months_continuous", "soft" if demo or not span else "hard", not gaps,
        "no empty month" if not gaps else f"empty: {', '.join(gaps[:6])}{'…' if len(gaps) > 6 else ''}",
        "every month between the first and the last archive has snapshots, per feed")
    add("months_no_dip", "soft", not dips, "none" if not dips else "; ".join(dips[:6]),
        "no month below 20 % of the trailing six-month median, per feed")

    through = _one(con, "select least(max(updated::timestamp)::date, ?::date) from snapshots "
                        "where updated::timestamp <= ?::timestamp + interval 1 day", expected, as_of)
    add("data_through", "soft", through is not None and through >= expected - timedelta(days=3),
        f"{through} (last closed month ends {expected})",
        "the newest snapshot is within three days of the end of the last closed month")

    # --- metric sanity: the regressions that bit before ------------------------------------------
    med1 = _one(con, "select median(discount) from lots where is_open and n_bids = 1 and discount is not null and is_health")
    add("single_bid_median_discount", "soft" if demo else "hard", med1 is not None and med1 <= 0.02,
        f"{med1:.3f}" if med1 is not None else "n/a", "median discount with one bid <= 2 % (the 76 % artefact of 2026-10-09)")
    sb = _one(con, "select avg((n_bids = 1)::int) from lots where is_open and n_bids is not null and is_health")
    add("single_bid_rate_plausible", "soft", sb is not None and 0.2 <= sb <= 0.7, f"{sb:.3f}" if sb is not None else "n/a",
        "national open-procedure single-bid rate between 20 % and 70 %")
    lb = _one(con, "select avg((lot_budget is not null)::int) from lots where source_feed = '1044'")
    add("feed_1044_lot_budgets", "soft", True, f"{lb:.1%} of feed-1044 lots have a budget" if lb is not None else "n/a",
        "informative: regional platforms publish no lot list, so multi-lot folders have no per-lot budget")
    out += stability(con, previous)
    return out


def stability(con: duckdb.DuckDBPyConnection, previous: Path | None) -> list[Check]:
    """Metric stability against the spine that is live now: a refresh may add a month of data, it must not
    reorder the buyers. Soft checks; skipped when there is no previous spine or it is the same file."""
    if previous is None or not previous.exists():
        return []
    try:
        con.execute(f"attach '{previous.resolve()}' as prev (read_only)")
    except duckdb.Error as e:
        return [Check("metric_stability", "soft", True, f"previous spine not comparable ({type(e).__name__})",
                      "rank correlation of buyer single-bid rates with the live spine >= 0.9")]
    try:
        rows = _stability_rows(con)
        n_cur = _one(con, "select count(*) from lots")
        n_old = _one(con, "select count(*) from prev.lots")
    except duckdb.Error as e:
        return [Check("metric_stability", "soft", True, f"previous spine not comparable ({type(e).__name__})",
                      "rank correlation of buyer single-bid rates with the live spine >= 0.9")]
    finally:
        con.execute("detach prev")
    rho, n = rows
    out = [Check("metric_stability", "soft", rho is None or rho >= 0.9,
                 f"Spearman {rho:.3f} over {n} buyers" if rho is not None else "no common buyers",
                 "rank correlation of buyer single-bid rates with the live spine >= 0.9")]
    delta = (n_cur - n_old) / max(n_old, 1)
    out.append(Check("lot_count_drift", "soft", abs(delta) <= 0.10, f"{n_old:,} -> {n_cur:,} ({delta:+.1%})",
                     "total awarded lots within ±10 % of the live spine (a month of data is ~1 %)"))
    return out


def _stability_rows(con) -> tuple:
    return con.execute("""
            with cur as (select buyer_key, avg((n_bids = 1)::int) r, count(*) n from lots
                         where is_open and n_bids is not null and is_health and award_d >= date '2022-01-01'
                         group by 1 having count(*) >= 100),
                 old as (select buyer_key, avg((n_bids = 1)::int) r from prev.lots
                         where is_open and n_bids is not null and is_health and award_d >= date '2022-01-01'
                         group by 1 having count(*) >= 100)
            select corr(rc, ro), count(*) from (
              select rank() over (order by cur.r) as rc, rank() over (order by old.r) as ro
              from cur join old using (buyer_key))""").fetchone()


def to_markdown(checks: list[Check]) -> str:
    lines = ["| check | level | status | value | rule |", "|---|---|---|---|---|"]
    lines += [f"| {c.name} | {c.level} | {c.status} | {c.value} | {c.rule} |" for c in checks]
    return "\n".join(lines) + "\n"


def failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.level == "hard" and not c.passed]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spine", type=Path, default=SPINES_DIR / "spine.duckdb")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = ap.parse_args(argv)
    con = duckdb.connect(str(args.spine), read_only=True)
    as_of = args.as_of or con.execute("select as_of from meta").fetchone()[0]
    checks = run(con, as_of)
    print(to_markdown(checks))
    bad = failures(checks)
    print(f"{len(checks)} checks, {len(bad)} hard failure(s), {sum(1 for c in checks if c.status == 'warn')} warning(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
