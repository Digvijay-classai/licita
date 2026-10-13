"""Read-only data access for the app: the spine (radar, curve) and the precomputed fact sheets."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from licita.errors import LicitaError
from licita.memo.facts import OUT_DIR, SPINE
from licita.profile import load_profile
from licita.spine.build import SPINE_SCHEMA

DUE_FLAGS = ("start_now", "due_6m", "verify", "lapsed")


def check_spine(spine: Path = SPINE) -> int:
    """The spine's schema version; refuses a missing or older spine with the command that fixes it."""
    if not spine.exists():
        raise LicitaError(f"no spine at {spine}", "the data has not been built for this environment",
                          "run `make demo` for synthetic data, or `make backfill build` for the real feeds")
    with duckdb.connect(str(spine), read_only=True) as con:
        cols = {c[0] for c in con.execute("select column_name from information_schema.columns "
                                          "where table_name = 'meta'").fetchall()}
        version = con.execute("select schema_version from meta").fetchone()[0] if "schema_version" in cols else 1
    if version < SPINE_SCHEMA:
        raise LicitaError(f"spine schema {version} is older than {SPINE_SCHEMA}", f"{spine.resolve().name} predates "
                          "a change in the spine tables", "rebuild required: `make build` (or `make demo-build`)")
    return version


def _con(spine: Path = SPINE) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(spine), read_only=True)


def as_of(spine: Path = SPINE) -> date:
    """The date the radar was computed for (spine build date)."""
    with _con(spine) as con:
        return con.execute("select as_of from meta").fetchone()[0]


def data_through(spine: Path = SPINE) -> date:
    """Latest PLACSP update actually in the data. This, not the build date, is what 'Datos a fecha' shows:
    a spine rebuilt today from last month's archives is not today's data."""
    with _con(spine) as con:
        return con.execute("select max(updated)::date from folders where updated <= current_date::varchar || 'Z'"
                           ).fetchone()[0]


def curve(cpv2: str = "**", spine: Path = SPINE) -> pd.DataFrame:
    """Median discount by bids received (1..7) from the bidder_value table; '**' is the national pool.
    Empty when the category has no curve."""
    with _con(spine) as con:
        bv = con.execute("select k, n_k, n_k1, median_k, median_k1 from bidder_value "
                         "where cpv2 = ? order by k", [cpv2]).df()
    if bv.empty:
        return pd.DataFrame(columns=["bids", "median_discount", "n"])
    rows = [{"bids": int(r.k), "median_discount": r.median_k, "n": int(r.n_k)} for r in bv.itertuples()]
    last = bv.iloc[-1]
    rows.append({"bids": int(last.k) + 1, "median_discount": last.median_k1, "n": int(last.n_k1)})
    return pd.DataFrame(rows)


def hospital_curve(hospital: str, spine: Path = SPINE, min_n: int = 5) -> pd.DataFrame:
    with _con(spine) as con:
        return con.execute("""select n_bids::int as bids, median(discount) as median_discount, count(*) as n
                              from lots where buyer_nif = ? and is_open and n_bids between 1 and 7
                                and discount is not null
                              group by 1 having count(*) >= ? order by 1""",
                           [load_profile().buyer(hospital).nif, min_n]).df()


def radar(hospital: str, spine: Path = SPINE, flags: tuple[str, ...] = DUE_FLAGS) -> pd.DataFrame:
    with _con(spine) as con:
        return con.execute(f"""select * from radar where hospital = ? and flag in ({','.join('?' * len(flags))})
                               order by start_by nulls last, annual_budget desc""", [hospital, *flags]).df()


def _has_table(con, name: str) -> bool:
    return bool(con.execute("select count(*) from information_schema.tables where table_name = ?", [name]).fetchone()[0])


def benchmark_region(nuts_prefix: str, spine: Path = SPINE) -> pd.DataFrame:
    """The region's health buyers from the benchmark table (hospital-like ones), the biggest value at risk
    first. Empty frame when the spine predates the table or the profile names no region."""
    if not nuts_prefix:
        return pd.DataFrame()
    with _con(spine) as con:
        if not _has_table(con, "benchmark"):
            return pd.DataFrame()
        prof = load_profile()
        nifs = [b.nif for b in prof.buyers.values()]
        central = prof.region.central_buyer_re
        # the profile's own buyers plus the region's central purchasing body; every hospital-like buyer of
        # the region only when the profile names fewer than three (demo, single-hospital deployments)
        if len(nifs) >= 3:
            return con.execute(f"""select * from benchmark where region like ? || '%'
                                   and (buyer_nif in ({','.join('?' * len(nifs))})
                                        or (? <> '' and regexp_matches(coalesce(buyer, ''), '(?i)' || ?)))
                                   order by past_term_no_successor_eur_yr desc nulls last, buyer""",
                               [nuts_prefix, *nifs, central, central]).df()
        return con.execute("""select * from benchmark where region like ? || '%' and hospital_like
                              order by past_term_no_successor_eur_yr desc nulls last, buyer""", [nuts_prefix]).df()


def centralisation_candidates(nuts_prefix: str, spine: Path = SPINE, since: str = "2022-01-01", limit: int = 15) -> pd.DataFrame:
    """Same object (CPV-4) bought by three or more of the region's health authorities from one supplier."""
    if not nuts_prefix:
        return pd.DataFrame()
    with _con(spine) as con:
        if not _has_table(con, "benchmark"):
            return pd.DataFrame()
        return con.execute("""
            with b as (select buyer_key from benchmark where region like ? || '%' and hospital_like)
            select l.cpv4, l.winner_nif, coalesce(s.canonical_name, any_value(l.winner)) as supplier,
                   count(distinct l.buyer_key) as n_buyers, sum(l.award_net) as awarded_eur,
                   avg((l.n_bids = 1)::int) as single_bid_share, count(*) as n_lots
            from lots l join b using (buyer_key) left join suppliers s using (winner_nif)
            where l.award_d >= ?::date and l.winner_nif is not null and coalesce(s.is_person, false) = false
            group by l.cpv4, l.winner_nif, s.canonical_name
            having count(distinct l.buyer_key) >= 3
            order by awarded_eur desc limit ?""", [nuts_prefix, since, limit]).df()


def fact_sheets(hospital: str, out_dir: Path = OUT_DIR) -> dict[str, dict]:
    """folder_id -> fact sheet, continuity first: files already late, then contracts past their initial
    term, then those due; within a group the latest file first, then annual budget; framework-derived last."""
    from licita.app.components import continuity_key
    sheets = [json.loads(p.read_text()) for p in sorted((out_dir / hospital / "facts").glob("*.json"))]
    sheets.sort(key=continuity_key)
    return {s["contract"]["folder_id"]: s for s in sheets}
