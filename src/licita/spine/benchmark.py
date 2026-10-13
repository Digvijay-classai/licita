"""Multi-buyer benchmark: one row per health buyer, built from the spine's own tables.

  python -m licita.spine.benchmark [--spine FILE] [--region ES99] [--since 2022-01-01]

Population: health buyers (`lots.is_health`) with at least `MIN_LOTS` awarded lots since `since`.

Definitions (same source tables and words as docs/engineering/DATA.md):

- **Recurring contract**: a folder whose lots have `duration_months >= 12` and whose contract type is not works
  ('3'); one row per (buyer_key, folder_id) with its CPV-4, `end_initial` (max over lots) and annual budget
  (sum over lots).
- **Successor**: the earliest folder of the same buyer and the same CPV-4 whose first notice (prior
  information notice, else call for tenders, else the folder's first snapshot) falls after `end_initial − 365`
  days. Self-contained and deliberately simple: no title similarity, no framework absorption (the radar's
  matcher does that per profile buyer; this is the cohort-wide proxy).
- **Ended contracts**: recurring contracts with `end_initial` between `since` and `as_of − 90` days.
  `on_time_share` = successor noticed at least 90 days before the end; `late_share` = successor noticed only
  after the end; `no_successor_share` = none seen. The three do not sum to one: the gap is "noticed inside the
  last 90 days".
- **past_term_no_successor_eur_yr**: annual budget of recurring contracts that ended in the last 365 days with
  no successor seen.
- **extension_award_share**: share of the buyer's awarded lots whose title starts with "prórroga" (an
  extension published as an award).
- **deserted_rate**: result code 3 ("no bids") over all results of the buyer's folders updated since `since`.
- **nsp_share**: value share of negotiated-without-publicity awards (procedure '3') since `since`.
- **single_bid_rate**: share of open-procedure lots (procedure 1 or 9, bid count known) with one bid.
  **expected_rate**: the mean peer-cell single-bid rate (`peers`: CPV-4 × value band, all health buyers) over
  the buyer's open lots that have a cell; `single_bid_rate_cells` is the buyer's own rate on that same subset,
  so `gap = single_bid_rate_cells − expected_rate` compares like with like. `gap_lo`/`gap_hi` are the Wilson
  95 % interval of the buyer's rate on that subset minus the expected rate.
- **shrunk_gap**: empirical-Bayes shrinkage of the gap toward the mean gap of comparable buyers in the same
  region (national mean when the region has none): `(n·gap + K·mean) / (n + K)` with `K = 50` open lots, so
  a buyer with 30 lots moves most of the way to its peers and one with 500 barely moves.
- **Continuity tier** (`cpv_tiers.csv`, longest CPV prefix wins; unknown = tier 3, low confidence): 1 stops
  care if it lapses, 2 degrades care or safety within weeks, 3 deferrable. Weights 1.0 / 0.6 / 0.2.
- **value_at_risk_eur_yr**: tier-weighted annual budget of (a) recurring contracts that ended in the last 365
  days with no successor and (b) recurring contracts ending in the next 270 days (a p80 lead time plus 90 days
  of preparation) with no successor noticed yet. `tier1_share` is the unweighted share of that value in tier 1.
- **coverage_score**: mean of five shares over the buyer's lots: budget present, real award date (not proxied),
  notice date present, duration present, not published through the aggregated regional feed (1044).
  **comparable** = coverage ≥ 0.6 and at least 30 open lots.
- **hospital_like**: the buyer's name carries a generic hospital or health-service word (regex in the module);
  municipal health departments, research foundations and tax or defence bodies buying medical goods stay in
  the table but flagged false, so a cohort can be filtered without naming anyone.
- **Bands** (per region, comparable buyers only, tertiles): `on_track` / `watch` / `behind`; everyone else
  `data_limited`. No ordinal rank is produced here.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import duckdb
import pandas as pd

from licita.config import SPINES_DIR
from licita.spine.regions import COVERAGE_FIELDS, coverage_score, normalise_city, region_for

MIN_LOTS = 30
MIN_OPEN_LOTS = 30
MIN_COVERAGE = 0.6
SHRINK_K = 50
PREP_DAYS = 90
DUE_DAYS = 270
TIER_WEIGHT = {1: 1.0, 2: 0.6, 3: 0.2}
TIERS_CSV = Path(__file__).with_name("cpv_tiers.csv")
EXTENSION_SQL = "regexp_matches(coalesce(title, ''), '^\\s*pr[oó]rroga', 'i')"
# Generic words that mark a hospital or a health-service purchasing body (accent-free, lower case). The flag
# narrows the population to care providers; municipal health departments and research bodies are health
# buyers too, but not the benchmark's subject.
HOSPITAL_RE = re.compile(r"hospital|clinic|sanitari|salut|saude|osasun|ingesa|area de salud"
                         r"|departamento de salud|servicio\w* de salud|salud(?! publica)")
# Generic nouns only: a regional health service's own naming conventions come from the profile
# (`region.health_terms`), never from this file.


# ----------------------------------------------------------------------------------------------- tiers
@cache
def load_tiers() -> pd.DataFrame:
    with open(TIERS_CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    df = pd.DataFrame(rows)
    df["tier"] = df["tier"].astype(int)
    return df


@cache
def _tier_index() -> dict[str, tuple[int, str]]:
    return {r.cpv_prefix: (int(r.tier), r.confidence) for r in load_tiers().itertuples()}


def tier_for(cpv: str | None) -> tuple[int, str]:
    """Longest matching prefix in cpv_tiers.csv; (3, 'low') when nothing matches."""
    code = re.sub(r"[^0-9]", "", cpv or "")
    idx = _tier_index()
    for n in range(len(code), 1, -1):
        hit = idx.get(code[:n])
        if hit:
            return hit
    return 3, "low"


# ----------------------------------------------------------------------------------------------- statistics
def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def shrink(value: float, n: int, prior_mean: float, k: int = SHRINK_K) -> float:
    return (n * value + k * prior_mean) / (n + k)


def bands(series: pd.Series, higher_is_better: bool) -> pd.Series:
    """Tertile bands among the given values: on_track / watch / behind."""
    s = series.dropna()
    out = pd.Series(index=series.index, dtype=object)
    if len(s) < 3:
        out[:] = "watch"
        out[series.isna()] = None
        return out
    lo, hi = s.quantile(1 / 3), s.quantile(2 / 3)
    for i, v in s.items():
        top = v >= hi if higher_is_better else v <= lo
        bottom = v <= lo if higher_is_better else v >= hi
        out[i] = "on_track" if top else ("behind" if bottom else "watch")
    return out


# ----------------------------------------------------------------------------------------------- metrics
def _prepare(con: duckdb.DuckDBPyConnection, since: date) -> None:
    con.execute("create or replace temp table bm_buyers as "
                "select buyer_key from lots where is_health and award_d >= ? group by 1 having count(*) >= ?",
                [since, MIN_LOTS])
    con.execute("""create or replace temp table bm_lots as
        select l.* from lots l join bm_buyers using (buyer_key) where l.is_health and l.award_d >= ?""", [since])
    con.execute("""create or replace temp table bm_contracts as
        select buyer_key, folder_id, any_value(cpv4) as cpv4, max(end_initial) as end_initial,
               sum(annual_budget) as annual_budget
        from lots l join bm_buyers using (buyer_key)
        where duration_months >= 12 and coalesce(contract_type, '') <> '3' and end_initial is not null
        group by 1, 2""")
    con.execute("""create or replace temp table bm_notices as
        select f.buyer_key, f.folder_id, left(f.cpvs, 4) as cpv4,
               coalesce(try_cast(f.notice_pin as date), try_cast(f.notice_cn as date),
                        try_cast(left(f.updated, 10) as date)) as first_seen
        from folders f join bm_buyers using (buyer_key)""")
    con.execute("""create or replace temp table bm_successors as
        select c.buyer_key, c.folder_id, c.cpv4, c.end_initial, c.annual_budget, min(n.first_seen) as successor_seen
        from bm_contracts c
        left join bm_notices n on n.buyer_key = c.buyer_key and n.cpv4 = c.cpv4 and n.folder_id <> c.folder_id
             and n.first_seen > c.end_initial - interval 365 day
        group by 1, 2, 3, 4, 5""")


def identity(con) -> pd.DataFrame:
    return con.execute(f"""
        select l.buyer_key, any_value(l.buyer_nif) as buyer_nif, arg_max(l.buyer, l.award_d) as buyer,
               arg_max(l.nuts, l.award_d) as nuts, any_value(f.buyer_city) as buyer_city,
               any_value(f.buyer_parents) as buyer_parents,
               avg((l.source_feed = '1044')::int) as feed_mix, count(*) as n_lots, sum(l.award_net) as awarded_eur,
               count(*) filter (where l.is_open and l.n_bids is not null) as n_open_lots,
               avg((l.lot_budget is not null)::int) as has_budget,
               avg((l.award_date_source = 'award_date')::int) as has_real_award_date,
               avg((l.notice_d is not null)::int) as has_notice_date,
               avg((l.duration_months is not null)::int) as has_duration,
               avg((l.source_feed <> '1044')::int) as not_feed_1044,
               avg(({EXTENSION_SQL})::int) as extension_award_share,
               sum(l.award_net) filter (where l.procedure = '3') / nullif(sum(l.award_net), 0) as nsp_share
        from bm_lots l
        left join (select buyer_key, arg_max(buyer_city, updated) as buyer_city,
                          arg_max(buyer_parents, updated) as buyer_parents from folders group by 1) f using (buyer_key)
        group by 1""").df()


def timeliness(con, as_of: date, since: date) -> pd.DataFrame:
    return con.execute("""
        with ended as (select * from bm_successors where end_initial between ? and ?)
        select buyer_key,
               count(*) as n_ended,
               avg((successor_seen <= end_initial - interval 90 day)::int) as on_time_share,
               avg((successor_seen > end_initial)::int) as late_share,
               avg((successor_seen is null)::int) as no_successor_share
        from ended group by 1""", [since, as_of - timedelta(days=PREP_DAYS)]).df()


def at_risk(con, as_of: date) -> pd.DataFrame:
    """Per buyer: past-term and due contracts with no successor, with their CPV-4 for tier weighting."""
    return con.execute(f"""
        select buyer_key, cpv4, annual_budget, end_initial < ? as past_term
        from bm_successors
        where successor_seen is null and annual_budget is not null
          and end_initial between ? - interval 365 day and ? + interval {DUE_DAYS} day""",
                       [as_of, as_of, as_of]).df()


def impact(con, as_of: date) -> pd.DataFrame:
    df = at_risk(con, as_of)
    if df.empty:
        return pd.DataFrame(columns=["buyer_key", "past_term_no_successor_eur_yr", "value_at_risk_eur_yr", "tier1_share"])
    df["tier"] = [tier_for(c)[0] for c in df.cpv4]
    df["weighted"] = df.annual_budget * df.tier.map(TIER_WEIGHT)
    df["tier1_value"] = df.annual_budget.where(df.tier == 1, 0.0)
    df["past_value"] = df.annual_budget.where(df.past_term, 0.0)
    g = df.groupby("buyer_key").agg(past_term_no_successor_eur_yr=("past_value", "sum"),
                                    value_at_risk_eur_yr=("weighted", "sum"), total=("annual_budget", "sum"),
                                    tier1=("tier1_value", "sum")).reset_index()
    g["tier1_share"] = g.tier1 / g.total.where(g.total > 0)
    return g.drop(columns=["total", "tier1"])


def deserted(con, since: date) -> pd.DataFrame:
    return con.execute("""
        select r.buyer_key, avg((r.result_code = '3')::int) as deserted_rate, count(*) as n_results
        from results r join bm_buyers using (buyer_key) join folders f using (buyer_key, folder_id)
        where f.updated >= ? group by 1""", [since.isoformat()]).df()


def competition(con) -> pd.DataFrame:
    df = con.execute("""
        with o as (select l.buyer_key, l.n_bids = 1 as one, p.single_bid_rate as exp
                   from bm_lots l left join peers p on p.cpv4 = l.cpv4 and p.band = l.band
                   where l.is_open and l.n_bids is not null)
        select buyer_key, avg(one::int) as single_bid_rate,
               count(*) filter (where exp is not null) as n_cells,
               sum(one::int) filter (where exp is not null) as k_cells,
               avg(one::int) filter (where exp is not null) as single_bid_rate_cells,
               avg(exp) as expected_rate
        from o group by 1""").df()
    lo_hi = [wilson(int(k) if pd.notna(k) else 0, int(n) if pd.notna(n) else 0)
             for k, n in zip(df.k_cells, df.n_cells, strict=True)]
    df["gap"] = df.single_bid_rate_cells - df.expected_rate
    df["gap_lo"] = [lh[0] for lh in lo_hi] - df.expected_rate
    df["gap_hi"] = [lh[1] for lh in lo_hi] - df.expected_rate
    return df


# ----------------------------------------------------------------------------------------------- assembly
def _profile():
    try:
        from licita.profile import load_profile
        return load_profile()
    except Exception:          # a missing profile must not break the benchmark
        return None


def build_benchmark(con: duckdb.DuckDBPyConnection, as_of: date, since: date = date(2022, 1, 1)) -> pd.DataFrame:
    _prepare(con, since)
    df = identity(con)
    if df.empty:
        return df
    for part in (timeliness(con, as_of, since), impact(con, as_of), deserted(con, since), competition(con)):
        df = df.merge(part, on="buyer_key", how="left")
    prof = _profile()
    extra = [normalise_city(t) for t in prof.region.health_terms] if prof is not None else []
    df["hospital_like"] = [bool(HOSPITAL_RE.search(normalise_city(b))) or any(t in normalise_city(b) for t in extra)
                           for b in df.buyer]
    df["region"] = [region_for(n, c, p, prof) for n, c, p in zip(df.nuts, df.buyer_city, df.buyer_parents, strict=True)]
    df["coverage_score"] = [coverage_score({k: r[k] for k in COVERAGE_FIELDS}) for _, r in df.iterrows()]
    df["comparable"] = (df.coverage_score >= MIN_COVERAGE) & (df.n_open_lots >= MIN_OPEN_LOTS)
    for col in ("past_term_no_successor_eur_yr", "value_at_risk_eur_yr"):
        df[col] = df[col].fillna(0.0)

    # shrinkage toward the regional mean gap of comparable buyers (national mean as fallback)
    comp = df[df.comparable & df.gap.notna()]
    national = float(comp.gap.mean()) if not comp.empty else 0.0
    regional = comp.groupby("region").gap.mean().to_dict()
    df["shrunk_gap"] = [shrink(g, int(n or 0), regional.get(r, national)) if pd.notna(g) else None
                        for g, n, r in zip(df.gap, df.n_cells, df.region, strict=True)]

    # bands per region among comparable buyers
    for metric, better_high, col in (("on_time_share", True, "band_timeliness"),
                                     ("shrunk_gap", False, "band_competition"),
                                     ("deserted_rate", False, "band_deserted")):
        df[col] = "data_limited"
        for _, grp in df[df.comparable & df.region.notna()].groupby("region"):
            b = bands(grp[metric].astype(float), better_high)
            df.loc[b.index, col] = b.fillna("data_limited")
    df = df.drop(columns=["nuts", "buyer_city", "buyer_parents", "k_cells"])
    return df.sort_values(["region", "buyer"], na_position="last").reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spine", type=Path, default=SPINES_DIR / "spine.duckdb")
    ap.add_argument("--since", type=date.fromisoformat, default=date(2022, 1, 1))
    ap.add_argument("--region", default=None, help="NUTS-2 code to print (the profile region by default)")
    args = ap.parse_args(argv)
    con = duckdb.connect(str(args.spine), read_only=True)
    as_of = con.execute("select as_of from meta").fetchone()[0]
    df = build_benchmark(con, as_of, args.since)
    cols = ["buyer", "region", "hospital_like", "n_lots", "n_open_lots", "on_time_share", "late_share", "past_term_no_successor_eur_yr",
            "value_at_risk_eur_yr", "single_bid_rate", "expected_rate", "gap", "shrunk_gap", "deserted_rate",
            "coverage_score", "comparable", "band_timeliness", "band_competition"]
    view = df[df.region == args.region] if args.region else df
    with pd.option_context("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 40):
        print(view[cols].round(3).to_string(index=False))
    comp = df[df.comparable]
    print(f"\n{len(df)} buyers, {len(comp)} comparable; on_time_share p10/p50/p90 among comparable: "
          f"{comp.on_time_share.quantile(0.1):.2f} / {comp.on_time_share.median():.2f} / {comp.on_time_share.quantile(0.9):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
