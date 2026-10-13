"""Expiry radar per hospital: successor status + when re-tendering should start.

flag values (UI maps them to i18n status labels):
  covered        a successor exists (replaced / extended / absorbed_by_framework / in_progress)
  start_now      no confirmed successor and the start-by date has passed
  due_6m         start-by date within 6 months
  verify         contract already ended and successor is uncertain: a human must check
  ok             nothing to do yet

Ground truth (`licita.spine.truth`): when the procurement team has answered for a contract, its answer
overrides the heuristic match (`apply_truth`); `heuristic_status` / `heuristic_flag` keep what the matcher
said, and `matcher_precision` measures the agreement between the two.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, timedelta

import duckdb
import pandas as pd

from licita.profile import Buyer, load_profile
from licita.spine.successors import Candidate, Contract, match

PREP_DAYS = 90       # internal preparation before publication; assumption until discovery confirms
MIN_RECURRING_MONTHS = 6
COVERED = {"replaced", "replaced_late", "extended", "absorbed_by_framework", "in_progress"}
LAPSED_DAYS = 365    # ended longer ago than this with no successor: history to confirm, not an alert
# One-time equipment purchases: no recurring need to re-tender (checked only when duration is missing or short).
EQUIPMENT_RE = re.compile(
    r"\b(suministro|adquisici[oó]n|contrataci[oó]n)\b.{0,60}\b(equipos?|ec[oó]grafos?|ecograf[ií]a|mam[oó]grafo"
    r"|tac\b|esc[aá]ner|microscopio|mobiliario|veh[ií]culo|ambulancia)",
    re.IGNORECASE)


def _d(x) -> date | None:
    if x is None or pd.isna(x):
        return None
    return x.date() if hasattr(x, "date") else x


def contracts_for(con: duckdb.DuckDBPyConnection, nif: str) -> pd.DataFrame:
    return con.execute("""
        select buyer_nif, folder_id, any_value(title) as title, any_value(cpvs) as cpvs, any_value(cpv2) as cpv2,
               min(award_d) as award_d, max(end_initial) as end_initial, max(end_max) as end_max,
               max(n_lots) as n_lots, sum(award_net) as award_net, min(n_bids) as min_bids, max(n_bids) as max_bids,
               any_value(procedure) as procedure, any_value(link) as link, any_value(brand_in_title) as brand_in_title,
               sum(annual_budget) as annual_budget, any_value(winner) as winner,
               any_value(contract_type) as contract_type, max(duration_months) as duration_months
        from lots where buyer_nif = ? group by buyer_nif, folder_id
    """, [nif]).df()


def candidates_for(con: duckdb.DuckDBPyConnection, nif: str) -> list[Candidate]:
    """Successor candidates: this buyer's folders plus those of the profile's central purchasing bodies."""
    central = load_profile().region.central_buyer_re
    df = con.execute("""
        with fs as (
          select buyer_key, folder_id,
                 min(coalesce(try_cast(notice_pin as date), try_cast(notice_cn as date),
                              try_cast(left(updated, 10) as date))) as first_seen
          from snapshots group by buyer_key, folder_id)
        select f.buyer_nif, f.buyer, f.folder_id, f.title, f.cpvs, fs.first_seen, f.status, f.procedure, f.n_lots
        from folders f join fs using (buyer_key, folder_id)
        where f.buyer_nif = ?
           or (? <> '' and regexp_matches(coalesce(f.buyer, ''), '(?i)' || ?))
    """, [nif, central, central]).df()
    return [Candidate(r.buyer_nif, r.buyer or "", r.folder_id, r.title or "",
                      frozenset(c[:4] for c in (r.cpvs or "").split(",") if c), _d(r.first_seen), r.status,
                      r.procedure, int(r.n_lots or 0)) for r in df.itertuples()]


# team answer -> (successor_status, flag or None to keep the computed one)
TRUTH_MAP = {
    "extended": ("extended_confirmed", "covered"),
    "extension_pending": ("extension_pending", "verify"),
    "replaced": ("replaced_confirmed", "covered"),
    "replaced_by_framework": ("absorbed_by_framework_confirmed", "covered"),
    "in_progress": ("in_progress_confirmed", "covered"),
    "lapsed": ("lapsed_confirmed", None),
    "ended_no_need": ("ended_no_need", "ok"),
}
TRUTH_COLS = ["truth_status", "truth_source", "truth_recorded_on", "heuristic_status", "heuristic_flag"]


def apply_truth(radar: pd.DataFrame, truth: pd.DataFrame | None, as_of: date) -> pd.DataFrame:
    """Let the team's answer override the matcher. Matches on (hospital key or buyer NIF, folder_id).

    extended: covered, months_to_end from extended_until; an extension already past is 'extension_expired'
    and goes back to verify. lapsed: start_now when the contract has ended, otherwise the computed flag.
    """
    df = radar.copy()
    if df.empty:
        for col in TRUTH_COLS:
            df[col] = pd.Series(dtype=object)
        return df
    df["heuristic_status"] = df["successor_status"]
    df["heuristic_flag"] = df["flag"]
    for col in ("truth_status", "truth_source", "truth_recorded_on"):
        df[col] = None
    if truth is None or truth.empty:
        return df
    by_key = {(str(r.buyer_key), str(r.folder_id)): r for r in truth.itertuples(index=False)}
    for i in df.index:
        row = df.loc[i]
        t = by_key.get((str(row["hospital"]), str(row["folder_id"]))) or \
            by_key.get((str(row.get("buyer_nif")), str(row["folder_id"])))
        if t is None:
            continue
        status, flag = TRUTH_MAP[t.status]
        end = _d(row.get("end_initial"))
        if t.status == "extended":
            until = _d(t.extended_until)
            if until is not None:
                df.at[i, "months_to_end"] = round((until - as_of).days / 30.4, 1)
                if until < as_of:
                    status, flag = "extension_expired", "verify"
        elif t.status == "lapsed":
            flag = "start_now" if end is None or end < as_of else None
        if getattr(t, "successor_folder_id", None):
            df.at[i, "successor_folder"] = t.successor_folder_id
        df.at[i, "successor_status"] = status
        df.at[i, "match_confidence"] = 1.0
        if flag is not None:
            df.at[i, "flag"] = flag
        df.at[i, "truth_status"] = t.status
        df.at[i, "truth_source"] = t.source
        df.at[i, "truth_recorded_on"] = t.recorded_on
    return df


# truth vocabulary -> the matcher statuses that mean the same thing
_AGREE = {
    "extended": {"extended"},
    "replaced": {"replaced", "replaced_late", "replaced_partial"},
    "replaced_by_framework": {"absorbed_by_framework"},
    "in_progress": {"in_progress"},
}


def matcher_precision(radar: pd.DataFrame) -> dict:
    """Agreement between the heuristic match and the team's answer, where both exist.

    For 'extended' and 'replaced' (and 'framework'): precision = of the contracts the matcher called X, how
    many the team confirmed; recall = of the contracts the team called X, how many the matcher found."""
    if "truth_status" not in radar.columns:
        return {"n_truth": 0}
    t = radar[radar["truth_status"].notna()]
    out: dict = {"n_truth": int(len(t))}
    agree = sum(h in _AGREE.get(s, set()) for h, s in zip(t["heuristic_status"], t["truth_status"], strict=True))
    out["agreement"] = round(agree / len(t), 3) if len(t) else None
    for label, truth_key in (("extended", "extended"), ("replaced", "replaced"), ("framework", "replaced_by_framework")):
        heur = t["heuristic_status"].isin(_AGREE[truth_key])
        real = t["truth_status"] == truth_key
        tp, fp, fn = int((heur & real).sum()), int((heur & ~real).sum()), int((~heur & real).sum())
        out[label] = {"tp": tp, "fp": fp, "fn": fn,
                      "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                      "recall": round(tp / (tp + fn), 3) if tp + fn else None}
    return out


def _truth_from(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    tables = {r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()}
    return con.execute("select * from truth").df() if "truth" in tables else pd.DataFrame()


def build_radar(con: duckdb.DuckDBPyConnection, hospitals: Mapping[str, Buyer], as_of: date,
                truth: pd.DataFrame | None = None) -> pd.DataFrame:
    if truth is None:
        truth = _truth_from(con)
    lead = dict(con.execute("select cpv2, p80_days from lead_times").fetchall())
    fallback = con.execute("select quantile_cont(p80_days, 0.5) from lead_times").fetchone()[0] or 180
    rows = []
    for key, h in hospitals.items():
        cands = candidates_for(con, h.nif)
        for r in contracts_for(con, h.nif).itertuples():
            c = Contract(r.buyer_nif, r.folder_id, r.title or "",
                         frozenset(x[:4] for x in (r.cpvs or "").split(",") if x),
                         _d(r.award_d), _d(r.end_initial), _d(r.end_max), int(r.n_lots or 0))
            m = match(c, cands, open_ended=True)
            lead_days = int(lead.get(r.cpv2, fallback))
            end = c.end_initial
            start_by = end - timedelta(days=lead_days + PREP_DAYS) if end else None
            no_duration = r.duration_months is None or pd.isna(r.duration_months)
            one_off = r.contract_type == "3" or (
                not no_duration and r.duration_months < MIN_RECURRING_MONTHS) or (
                (no_duration or r.duration_months <= MIN_RECURRING_MONTHS) and bool(EQUIPMENT_RE.search(r.title or "")))
            if one_off:
                flag = "one_off"
            elif m.status in COVERED:
                flag = "covered"
            elif end is None:
                flag = "verify"
            elif end < as_of - timedelta(days=LAPSED_DAYS):
                flag = "lapsed"            # ended long ago, no successor found: history for the hospital to confirm
            elif end < as_of:
                flag = "verify"            # ended recently without a confirmed successor
            elif start_by <= as_of:
                flag = "start_now"
            elif start_by <= as_of + timedelta(days=183):
                flag = "due_6m"
            else:
                flag = "ok"
            rows.append({
                "hospital": key, **r._asdict(), "successor_status": m.status, "match_confidence": m.confidence,
                "successor_folder": m.successor_folder, "successor_title": m.successor_title,
                "lead_days": lead_days, "prep_days": PREP_DAYS, "start_by": start_by, "flag": flag,
                "months_to_end": round((end - as_of).days / 30.4, 1) if end else None,
            })
    df = pd.DataFrame(rows).drop(columns=["Index"], errors="ignore")
    df = apply_truth(df, truth, as_of)
    con.register("radar_df", df)
    con.execute("create or replace table radar as select * from radar_df")
    return df
