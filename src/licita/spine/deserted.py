"""Deserted lots: what nobody bid for, whether it came back, and what the market looks like elsewhere.

A deserted lot (PLACSP result code 3: no bid received; 4 and 5: withdrawn or not awarded) is more than a
lost tender. It forces a negotiated procedure with the buyer's leverage gone, while the ward still needs the
supply. The tracker gives, per deserted lot:

  budget            the lot's own budget from the folder's lot list, or the folder budget for a single lot
  deserted_date     the result's award date, else the contract date, else the folder's award notice, else
                    the snapshot date
  republication     a later folder of the same buyer, first seen within 12 months, whose boilerplate-free
                    title shares >= 50 % of its character 3-grams with the deserted one (the successor
                    matcher's measure), or >= 25 % when it also shares the CPV-4; its outcome (awarded, with
                    the bids it drew; deserted again; still open)
  market            median bids and single-bid share for that CPV-4 across health buyers' open lots: whether
                    the object is bought competitively elsewhere, which is the first question to ask

`summary` folds it to buyer × year for the regional view.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from licita.spine.successors import grams, jaccard, normalise

WINDOW_MONTHS = 12
SIMILAR = 0.5            # same object, republished
SIMILAR_SAME_CPV = 0.25  # same CPV-4 and a visibly related title
PREFILTER_JW = 0.8       # SQL pre-filter on Jaro-Winkler so Python only scores plausible pairs
NON_AWARD_CODES = ("3", "4", "5")
AWARD_CODES = ("2", "8", "9", "11")

DESERTED_SQL = f"""
with lb as (
  select f.buyer_key, f.folder_id, l.lot_id, l.budget_net as lot_budget
  from folders f, unnest(from_json(f.lots_json, '[{{"lot_id":"VARCHAR","budget_net":"DOUBLE"}}]')) as t(l)
  where f.lots_json is not null)
select r.buyer_key, f.buyer_nif, r.folder_id, r.lot_id, r.result_code, f.title, left(f.cpvs, 4) as cpv4,
       f.procedure, f.source_feed,
       coalesce(lb.lot_budget, case when f.n_lots <= 1 then f.budget_net end) as budget,
       coalesce(try_cast(r.award_date::varchar as date), try_cast(left(r.contract_date::varchar, 10) as date),
                try_cast(f.notice_award::varchar as date), try_cast(left(r.updated::varchar, 10) as date)) as deserted_date
from results r
join folders f using (buyer_key, folder_id)
left join lb on lb.buyer_key = r.buyer_key and lb.folder_id = r.folder_id and lb.lot_id = r.lot_id
where r.result_code in {NON_AWARD_CODES}
"""

# Candidate republications: same buyer, first seen after the deserted date and within the window, and a
# title that is at least loosely similar (Jaro-Winkler on folded titles) or shares the CPV-4.
CANDIDATES_SQL = f"""
with d as (select distinct buyer_key, folder_id, title, cpv4, deserted_date from deserted_df
           where deserted_date is not null),
     fs as (select buyer_key, folder_id, any_value(title) as title, left(any_value(cpvs), 4) as cpv4,
                   min(coalesce(try_cast(notice_pin::varchar as date), try_cast(notice_cn::varchar as date),
                                try_cast(left(updated::varchar, 10) as date))) as first_seen
            from snapshots where folder_id is not null group by 1, 2)
select d.buyer_key, d.folder_id, d.title, d.cpv4, c.folder_id as cand_folder, c.title as cand_title,
       c.cpv4 as cand_cpv4, c.first_seen
from d join fs c on c.buyer_key = d.buyer_key and c.folder_id <> d.folder_id
   and c.first_seen > d.deserted_date and c.first_seen <= d.deserted_date + interval {WINDOW_MONTHS} month
   and (c.cpv4 = d.cpv4
        or jaro_winkler_similarity(strip_accents(lower(c.title)), strip_accents(lower(d.title))) >= {PREFILTER_JW})
"""

OUTCOME_SQL = f"""
select buyer_key, folder_id,
       case when count(*) filter (where result_code in {AWARD_CODES}) > 0 then 'awarded'
            when count(*) filter (where result_code in {NON_AWARD_CODES}) > 0 then 'deserted_again'
            else 'open' end as outcome,
       median(n_bids) filter (where result_code in {AWARD_CODES}) as bids_after
from results where (buyer_key, folder_id) in (select buyer_key, cand_folder from matches_df) group by 1, 2
"""

MARKET_SQL = """
select cpv4, count(*) as market_n, median(n_bids) as market_median_bids,
       avg((n_bids = 1)::int) as market_single_bid_rate
from lots where is_health and is_open and n_bids is not null group by 1
"""


def _best_match(group: pd.DataFrame) -> dict | None:
    g0 = grams(normalise(group.iloc[0]["title"]))
    best = None
    for c in group.itertuples():
        s = jaccard(g0, grams(normalise(c.cand_title)))
        same = c.cand_cpv4 == c.cpv4
        if s >= SIMILAR or (same and s >= SIMILAR_SAME_CPV):
            key = (s, same)
            if best is None or key > best[0]:
                best = (key, {"republished_folder": c.cand_folder, "republished_title": c.cand_title,
                              "republished_first_seen": c.first_seen, "similarity": round(s, 3),
                              "match_basis": "title" if s >= SIMILAR else "cpv4+title"})
    return best[1] if best else None


def track(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per deserted (or withdrawn) lot with budget, date, republication and market columns."""
    deserted = con.execute(DESERTED_SQL).df()
    if deserted.empty:
        return deserted
    con.register("deserted_df", deserted)
    cands = con.execute(CANDIDATES_SQL).df()
    matches = []
    if not cands.empty:
        for (bk, fid), g in cands.groupby(["buyer_key", "folder_id"], sort=False):
            m = _best_match(g)
            if m:
                matches.append({"buyer_key": bk, "folder_id": fid, "cand_folder": m["republished_folder"], **m})
    mdf = pd.DataFrame(matches, columns=["buyer_key", "folder_id", "cand_folder", "republished_folder",
                                         "republished_title", "republished_first_seen", "similarity", "match_basis"])
    con.register("matches_df", mdf)
    outcomes = con.execute(OUTCOME_SQL).df() if not mdf.empty else pd.DataFrame(
        columns=["buyer_key", "folder_id", "outcome", "bids_after"])
    market = con.execute(MARKET_SQL).df()
    con.unregister("deserted_df")
    con.unregister("matches_df")

    out = deserted.merge(mdf.drop(columns=["cand_folder"]), on=["buyer_key", "folder_id"], how="left")
    out = out.merge(outcomes.rename(columns={"folder_id": "republished_folder"}),
                    on=["buyer_key", "republished_folder"], how="left")
    out = out.merge(market, on="cpv4", how="left")
    out["outcome"] = out["outcome"].fillna("not_republished")
    return out


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """Buyer × year: deserted lots, their budget, how many came back and how they ended."""
    if df.empty:
        return pd.DataFrame(columns=["buyer_key", "year", "n_deserted", "budget", "n_republished",
                                     "n_awarded_after", "n_deserted_again"])
    d = df.copy()
    d["year"] = pd.to_datetime(d["deserted_date"]).dt.year
    d["republished"] = d["republished_folder"].notna()
    d["awarded_after"] = d["outcome"] == "awarded"
    d["deserted_again"] = d["outcome"] == "deserted_again"
    g = d.groupby(["buyer_key", "year"], dropna=False).agg(
        n_deserted=("folder_id", "size"), budget=("budget", "sum"), n_republished=("republished", "sum"),
        n_awarded_after=("awarded_after", "sum"), n_deserted_again=("deserted_again", "sum")).reset_index()
    for c in ("n_republished", "n_awarded_after", "n_deserted_again"):
        g[c] = g[c].astype(int)
    return g
