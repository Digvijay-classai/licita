"""Build a versioned spine from parsed PLACSP parquet and swap it in atomically (design review 1A).

  python -m licita.spine.build [--as-of 2026-10-08]

Steps:
  1. Load every snapshot once (the same snapshot sits in overlapping archives), normalise identifiers and
     resolve each buyer to one key (licita.spine.buyers). Keep the latest snapshot per (buyer_key, folder_id).
  2. Awards: the union over a folder's snapshots, one version per (lot, winner) taken from the newest snapshot
     that mentions the pair. Large frameworks publish their awards in pages across snapshots, so the latest
     snapshot alone dropped awards (see the build report's `awards_recovered`). Non-award rows (deserted,
     withdrawn) and annulled folders come from the latest snapshot only, so the current state is what is shown.
  3. Coverage gates with fixed floors (1C / T7#13). Fail loudly, naming the field.
  4. Run views.sql, then materialise every consumer-facing view into a table; suppliers dimension.
  5. Bidder-value curve, pooled by CPV-2 with bootstrap CIs (T7#10).
  6. Smoke test and the data-quality gate (licita.spine.dq); write the reports; swap the symlink.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from licita.config import DATA_DIR, PARQUET_DIR, RUNS_DIR, SPINES_DIR
from licita.profile import load_profile
from licita.spine import deserted, dq, reasons
from licita.spine import truth as truth_mod
from licita.spine.benchmark import build_benchmark
from licita.spine.brands import BRANDS
from licita.spine.buyers import KeyRow, kind, resolve
from licita.spine.radar import build_radar, matcher_precision

VIEWS_SQL = Path(__file__).with_name("views.sql")

# Fixed per-field coverage floors, measured on 2017–2026 health rows. A drop below a floor means
# the feed format changed or parsing broke; never ship a spine like that.
# NIF is only promised by feed 643 from 2018 (2017 used an older format; feed 1044 never has it),
# so NIF is gated on that slice; every row must still get a buyer_key.
FOLDER_FLOORS = {"folder_id": 1.0, "buyer_key": 1.0, "buyer_nif": 0.99, "cpvs": 0.95, "procedure": 0.95,
                 "budget_net": 0.95}
RESULT_FLOORS = {"award_date": 0.95, "n_bids": 0.85, "result_code": 0.99}
GATED_SLICE = "source_feed = '643' and updated >= '2018'"

BUYER_KEY_SQL = """coalesce(buyer_nif, 'DIR3:' || buyer_dir3,
    'NAME:' || lower(regexp_replace(trim(coalesce(buyer, '') || ' | ' || coalesce(buyer_parents, '')), '\\s+', ' ', 'g')))"""

SPINE_SCHEMA = 3   # bump when tables or columns change; readers refuse an older spine ("rebuild required")
AWARD_CODES = "('2', '8', '9', '11')"   # awarded / finally awarded / formalised (mirrored in views.sql)
MAX_K = 7  # the 8+ bids bucket is dominated by unit-price/framework tenders (budget = ceiling)


class CoverageError(Exception):
    """A field's coverage fell below its fixed floor."""


class SmokeTestError(Exception):
    """The new spine failed a basic sanity check and was not swapped in."""


def ext_months(text: str | None) -> float | None:
    """Maximum extension length from free text ('prorrogable por 2 años', '24 meses')."""
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(a[ñn]os?|anualidad(?:es)?|mes(?:es)?|d[ií]as?)", text.lower())
    if not m:
        return None
    n = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit.startswith(("a", "anual")):
        return n * 12
    return n if unit.startswith("mes") else n / 30.4


def brand_pattern() -> str:
    names = sorted({b.lower() for b in BRANDS}, key=len, reverse=True)
    return r"\b(" + "|".join(re.escape(n) for n in names) + r")\b|\bmarca\b"


def coverage(con: duckdb.DuckDBPyConnection) -> dict:
    out = {}
    for table, floors in (("folders", FOLDER_FLOORS), ("results", RESULT_FLOORS)):
        for field, floor in floors.items():
            where = GATED_SLICE if table == "folders" else "entry_id in (select entry_id from folders where " + GATED_SLICE + ")"
            val = con.execute(f"select avg(({field} is not null)::int) from {table} where {where}").fetchone()[0]
            out[f"{table}.{field}"] = {"coverage": round(val or 0, 4), "floor": floor}
    return out


def bidder_value(rate_lots: pd.DataFrame, n_boot: int = 300, min_n: int = 15, seed: int = 7) -> pd.DataFrame:
    """Median discount gain from k to k+1 bids, pooled by CPV-2 (and '**' nationally), with 90% CIs."""
    rng = np.random.default_rng(seed)
    df = rate_lots[(rate_lots.n_bids >= 1) & (rate_lots.n_bids <= MAX_K) & rate_lots.discount.notna()]
    rows = []
    for cpv2, g in [("**", df), *df.groupby("cpv2")]:
        by_k = {int(k): gk.discount.to_numpy() for k, gk in g.groupby("n_bids")}
        for k in range(1, MAX_K):
            a, b = by_k.get(k), by_k.get(k + 1)
            if a is None or b is None or len(a) < min_n or len(b) < min_n:
                continue
            boot = [np.median(rng.choice(b, len(b))) - np.median(rng.choice(a, len(a))) for _ in range(n_boot)]
            rows.append({
                "cpv2": cpv2, "k": k, "n_k": len(a), "n_k1": len(b),
                "median_k": float(np.median(a)), "median_k1": float(np.median(b)),
                "delta": float(np.median(b) - np.median(a)),
                "ci_lo": float(np.quantile(boot, 0.05)), "ci_hi": float(np.quantile(boot, 0.95)),
            })
    return pd.DataFrame(rows)


def smoke_test(con: duckdb.DuckDBPyConnection) -> dict:
    checks = {
        "lots": con.execute("select count(*) from lots").fetchone()[0],
        "rate_lots": con.execute("select count(*) from lots where is_open and n_bids is not null").fetchone()[0],
        "peers_cells": con.execute("select count(*) from peers").fetchone()[0],
        "bidder_value_rows": con.execute("select count(*) from bidder_value").fetchone()[0],
    }
    for key, b in load_profile().buyers.items():
        checks[f"buyer_{key}_lots"] = con.execute(
            "select count(*) from lots where buyer_nif = ?", [b.nif]).fetchone()[0]
    bad = {k: v for k, v in checks.items() if not v}
    if bad:
        raise SmokeTestError(f"empty results: {bad}")
    return checks


KEEP_SPINES = 3   # each spine is ~5 GB; keep the live one plus two rollbacks


def prune_old_spines(keep: int = KEEP_SPINES) -> list[str]:
    live = (SPINES_DIR / "spine.duckdb").resolve().name
    files = sorted(SPINES_DIR.glob("spine_*.duckdb"), reverse=True)
    removed = []
    for f in files[keep:]:
        if f.name != live:
            f.unlink()
            removed.append(f.name)
    return removed


def swap(new_file: Path) -> None:
    link = SPINES_DIR / "spine.duckdb"
    tmp = SPINES_DIR / "spine.duckdb.tmp"
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(new_file.name)
    os.replace(tmp, link)  # atomic on POSIX


def resolve_buyers(con: duckdb.DuckDBPyConnection) -> dict:
    """Write `buyer_xref` (raw key -> canonical key, canonical NIF) from `snapshots_raw`; return merge stats."""
    rows = [KeyRow(*r) for r in con.execute("""
        select buyer_key_raw, buyer_nif, buyer_dir3, lower(trim(coalesce(buyer, ''))),
               lower(trim(coalesce(buyer_city, ''))), lower(trim(split_part(coalesce(buyer_parents, ''), ' > ', 1))),
               count(*)::int
        from snapshots_raw group by all""").fetchall()]
    pairs = con.execute("select distinct buyer_nif, buyer_dir3 from snapshots_raw "
                        "where buyer_nif is not null and buyer_dir3 is not null").fetchall()
    mapping, stats = resolve(rows, pairs)
    xref = pd.DataFrame([{"buyer_key_raw": raw, "buyer_key": key, "nif": key if kind(key) == "nif" else None}
                         for raw, key in mapping.items()])
    con.register("xref_df", xref)
    con.execute("create table buyer_xref as select * from xref_df")
    return stats


def materialise_core(con: duckdb.DuckDBPyConnection, parquet_glob: str) -> dict:
    """snapshots_raw/results_raw (parquet) -> buyer_xref, snapshots, folders, results. Returns build stats."""
    con.execute(f"""create table snapshots_raw as
        with s as (select * replace (nullif(upper(trim(buyer_nif)), '') as buyer_nif, nullif(trim(buyer_dir3), '') as buyer_dir3)
                   from read_parquet('{parquet_glob}_folders.parquet', union_by_name=true))
        select *, {BUYER_KEY_SQL} as buyer_key_raw from s""")
    con.execute(f"create table results_raw as select * from read_parquet('{parquet_glob}_results.parquet', union_by_name=true)")
    stats = resolve_buyers(con)
    # one row per snapshot (overlapping archives repeat them), carrying the resolved buyer key and NIF
    con.execute("""create table snapshots as
        select s.* replace (coalesce(s.buyer_nif, x.nif) as buyer_nif), x.buyer_key
        from snapshots_raw s join buyer_xref x using (buyer_key_raw)
        qualify row_number() over (partition by s.entry_id, s.updated) = 1""")
    con.execute("drop table snapshots_raw")
    con.execute("""create table folders as
        select * exclude (rn) from (
          select *, count(*) over (partition by buyer_key, folder_id) as n_snapshots,
                 row_number() over (partition by buyer_key, folder_id order by updated desc, entry_id) as rn
          from snapshots where folder_id is not null)
        where rn = 1""")
    con.execute(f"""create table results as
        with s as (select entry_id, updated, buyer_key, status as snapshot_status from snapshots),
             f as (select buyer_key, folder_id, updated as latest_updated, coalesce(status, '') as latest_status from folders),
             r as (select distinct r.* replace (nullif(upper(trim(r.winner_nif::varchar)), '') as winner_nif,
                                                r.lot_id::varchar as lot_id, r.contract_id::varchar as contract_id),
                          s.buyer_key, s.snapshot_status, f.latest_updated, f.latest_status
                   from results_raw r join s using (entry_id, updated) join f using (buyer_key, folder_id)),
             is_award as (select *, result_code::varchar in {AWARD_CODES} and winner is not null and latest_status <> 'ANUL' as award from r),
             -- One version per (lot, winner): the newest snapshot that mentions the pair, with every row that
             -- snapshot holds for it (call-offs listed as separate contracts stay). An award republished as a
             -- formalisation (new result code, contract id filled in) is therefore one row, not two.
             version as (select * from is_award where award
                         qualify updated = max(updated) over (partition by buyer_key, folder_id, coalesce(lot_id, ''),
                                                                           coalesce(winner_nif, winner))),
             -- within that snapshot the same (lot, winner, contract id) can still appear twice (an award row
             -- and its formalisation row): keep the most advanced one
             awards as (select * from version
                        qualify row_number() over (partition by buyer_key, folder_id, coalesce(lot_id, ''),
                                                                coalesce(winner_nif, winner), coalesce(contract_id, '')
                                                   order by case result_code::varchar when '11' then 0 when '9' then 1
                                                                 when '8' then 2 else 3 end,
                                                            contract_date desc nulls last, award_net desc nulls last) = 1),
             rest as (select * from is_award where not award and updated = latest_updated)
        select * exclude (latest_updated, latest_status, award), updated = latest_updated as from_latest_snapshot from awards
        union all
        select * exclude (latest_updated, latest_status, award), true from rest""")
    stats["awards_recovered"] = con.execute(
        f"select count(*) from results where not from_latest_snapshot and result_code in {AWARD_CODES}").fetchone()[0]
    return stats


def build(as_of: date) -> Path:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    SPINES_DIR.mkdir(parents=True, exist_ok=True)
    out = SPINES_DIR / f"spine_{run_id}.duckdb"
    con = duckdb.connect(str(out))
    con.create_function("ext_months", ext_months, ["VARCHAR"], "DOUBLE", null_handling="special")

    stats = materialise_core(con, f"{PARQUET_DIR}/*")

    cov = coverage(con)
    failed = {k: v for k, v in cov.items() if v["coverage"] < v["floor"]}
    if failed:
        con.close()
        out.unlink()
        raise CoverageError(f"coverage below floor: {failed}")

    con.execute("create table brand_regex as select ? as pattern", [brand_pattern()])
    con.execute(VIEWS_SQL.read_text())
    con.execute("create table lots as select * from v_contract_terms")
    con.execute("create table lead_times as select * from v_lead_times")
    con.execute("create table peers as select * from v_peers")
    rate = con.execute("select cpv2, n_bids, discount from lots where is_open and n_bids is not null and is_health").df()
    bv = bidder_value(rate)
    con.register("bv", bv)
    con.execute("create table bidder_value as select * from bv")
    # Suppliers: PLACSP spells the same company hundreds of ways; the NIF is the identity, the most
    # frequent spelling the display name. Natural persons are flagged so no consumer lists them.
    con.execute("""create table suppliers as
        with names as (select winner_nif, winner, count(*) as c from lots where winner_nif is not null group by 1, 2),
             canon as (select winner_nif, arg_max(winner, c) as canonical_name, count(*) as n_spellings from names group by 1)
        select l.winner_nif, c.canonical_name, c.n_spellings, count(*) as n_awards, sum(l.award_net) as total_award_net,
               count(distinct l.buyer_key) as n_buyers, min(l.award_d) as first_award, max(l.award_d) as last_award,
               regexp_matches(l.winner_nif, '^[0-9]{8}[A-Z]$|^[XYZ][0-9]{7}[A-Z]$') as is_person
        from lots l join canon c using (winner_nif) group by all""")
    # newest snapshot in the data, capped at the end of the last closed month (a monthly archive carries a few
    # entries from the first minutes of the next month)
    through = con.execute("select least(max(updated::timestamp)::date, ?::date) from snapshots "
                          "where updated::timestamp <= ?::timestamp + interval 1 day",
                          [dq.last_closed_month_end(as_of), as_of]).fetchone()[0]
    con.execute("create table meta as select ? as run_id, ?::date as as_of, ?::date as data_through, now() as built_at, "
                "? as schema_version", [run_id, as_of, through, SPINE_SCHEMA])
    # one row per health buyer: timeliness, competition vs expected, value at risk, coverage, bands
    bench = build_benchmark(con, as_of)
    con.register("bench_df", bench)
    con.execute("create table benchmark as select * from bench_df")
    con.unregister("bench_df")
    stats["benchmark_buyers"] = int(len(bench))
    stats["benchmark_comparable"] = int(bench.comparable.sum()) if len(bench) else 0
    # why one bid (rule-based, with the signals that fired) and the deserted-lot tracker
    rs = reasons.classify(con)
    con.register("reasons_df", rs)
    con.execute("create table reasons as select * from reasons_df")
    con.unregister("reasons_df")
    ds = deserted.track(con)
    con.register("deserted_df", ds)
    con.execute("create table deserted as select * from deserted_df")
    con.unregister("deserted_df")
    stats["single_bid_lots_classified"] = int(len(rs))
    stats["deserted_rows"] = int(len(ds))
    # what the procurement team told us that PLACSP does not publish; empty tables when no file exists
    truth_mod.write_table(con, truth_mod.load_truth(DATA_DIR / "truth"), "truth")
    truth_mod.write_table(con, truth_mod.load_outcomes(DATA_DIR / "truth"), "outcomes")
    radar = build_radar(con, load_profile().buyers, as_of)          # reads table `truth` itself
    stats["matcher_precision"] = matcher_precision(radar)
    for v in ("v_contract_terms", "v_lead_times", "v_peers", "v_rate_lots", "v_lots", "v_lot_budgets"):
        con.execute(f"drop view if exists {v}")

    checks = smoke_test(con)
    quality = dq.run(con, as_of, previous=SPINES_DIR / "spine.duckdb")
    con.close()
    bad = dq.failures(quality)
    if bad:
        d = RUNS_DIR / run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "dq_report.md").write_text(f"# Data-quality report {run_id} (spine NOT swapped)\n\n" + dq.to_markdown(quality))
        out.unlink()
        raise dq.DQError("quality gate failed, spine not swapped: " + "; ".join(f"{c.name}: {c.value}" for c in bad)
                         + f" (see {d / 'dq_report.md'})")
    swap(out)
    prune_old_spines()
    report(run_id, as_of, out, cov, checks, bv, radar, stats, quality)
    return out


def report(run_id: str, as_of: date, out: Path, cov: dict, checks: dict, bv: pd.DataFrame,
           radar: pd.DataFrame, stats: dict | None = None, quality: list[dq.Check] | None = None) -> None:
    d = RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    nat = bv[bv.cpv2 == "**"]
    if quality:
        (d / "dq_report.md").write_text(f"# Data-quality report {run_id}\n\n" + dq.to_markdown(quality))
    lines = [
        f"# Build report {run_id}", "", f"- spine: `{out.name}` (now live)", f"- as_of: {as_of}", "",
        "## Coverage (feed 643, 2018+; NIF not published before 2018 or by feed 1044)", "| field | coverage | floor |", "|---|---|---|",
        *[f"| {k} | {v['coverage']:.3f} | {v['floor']} |" for k, v in cov.items()],
        "", "## Smoke checks", *[f"- {k}: {v:,}" for k, v in checks.items()],
        "", "## Identity resolution and award retention",
        *[f"- {k}: {v:,}" if isinstance(v, int) else f"- {k}: {json.dumps(v, default=str)}" for k, v in (stats or {}).items()],
        "", "## Data-quality gate", *([dq.to_markdown(quality)] if quality else ["(not run)"]),
        "", "## National bidder-value curve (health, open procedures, k ≤ 7)",
        "| k→k+1 | n_k | median_k | median_k+1 | Δ | 90% CI |", "|---|---|---|---|---|---|",
        *[f"| {r.k}→{r.k + 1} | {r.n_k:,} | {r.median_k:.3f} | {r.median_k1:.3f} | {r.delta:+.3f} | "
          f"[{r.ci_lo:+.3f}, {r.ci_hi:+.3f}] |" for r in nat.itertuples()],
        "", "## Radar (contracts per hospital × flag / successor status)",
        "```", radar.groupby(["hospital", "flag"]).size().to_string(), "",
        radar.groupby(["hospital", "successor_status"]).size().to_string(), "```",
    ]
    (d / "build_report.md").write_text("\n".join(lines) + "\n")
    (d / "build.json").write_text(json.dumps({"run_id": run_id, "spine": out.name, "checks": checks, "coverage": cov,
                                              "stats": stats or {},
                                              "dq": [c.__dict__ for c in (quality or [])]}, default=str, indent=1))
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = ap.parse_args(argv)
    build(args.as_of)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
