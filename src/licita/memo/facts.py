"""Deterministic fact sheet behind each competition memo (no LLM).

  python -m licita.memo.facts [--buyer HOSP_A] [--flags start_now,due_6m]

For every radar contract the memo needs four numbers, each traceable to the spine:
  peer benchmark     single-bid rate / median bids / median discount, same CPV-4 + value band, health, open;
                     plus a topic peer group (same CPV-2 and the title's distinctive words), because buyers
                     often file specific contracts under generic codes (MRI maintenance as 50000000)
  hospital history   the same three numbers for this hospital's own past lots in that CPV-4
  bidder value       median discount gain from k to k+1 bids (CPV-2 curve, 90% bootstrap CI) and the
                     implied EUR per year at this contract's annual budget. Associational, not causal.
  invite list        companies that win this topic (else CPV-4) at other health buyers, never at this hospital

Guardrails baked in here rather than left to the narrative:
  - contracts derived from a framework agreement are marked: competition was set at framework level
  - incumbent-is-the-manufacturer maintenance is marked: the question becomes third-party servicing
  - natural persons (DNI/NIE winners) never appear in invite lists
  - a bidder-value step whose CI touches zero is reported as not significant
The memo narrative (a Mistral model, later) may only cite numbers present in this file.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from licita.config import OUT_DIR, SPINES_DIR
from licita.profile import load_profile
from licita.spine.successors import normalise

SPINE = SPINES_DIR / "spine.duckdb"
HISTORY_YEARS = 5
MIN_PEER_N = 20
MIN_TOPIC_N = 10           # topic peers are closer comparables, so a smaller group is still informative
INVITE_LIMIT = 10
MAX_K = 6                                   # bidder_value has steps k -> k+1 for k <= 6
TOPIC_MIN_DF = 30          # a topic word must be a real word in the corpus, not a typo or a model number
TOPIC_MAX_SHARE = 0.03     # ... and rare enough to mean something (not "hospital", "gestion")
TOPIC_CANDIDATES = 6       # rarest words tried: the pair of the two rarest, then each word alone
# Words that are rare in titles but say nothing about the object (legal filler, places, qualifiers).
TOPIC_STOP = {
    "tenga", "deber", "legal", "convencional", "adelante", "instalado", "instalada", "instalados",
    "instaladas", "realizacion", "realizar", "pacientes", "publico", "publica", "respetuoso", "medio",
    "ambiente", "prestar", "asistencia", "sanitaria", "sanitario", "marca", "modelo", "general",
    "generales", "gestion", "seguimiento", "procesos", "productos", "producto", "unidad", "diferentes",
    "distintos", "presente", "otros", "incluido", "incluidos", "necesarios", "necesario", "farmacia",
    "almacen", "dependiente", "ubicados", "ubicado", "existentes", "propiedad", "arrendamiento", "opcion",
    "compra", "cesion", "uso", "conjunto", "asociado", "asociados", "nacional", "sistema",
    "principio", "activo", "medicamento", "medicamentos", "portal", "primaria", "pertenecientes",
    "perteneciente", "bienes", "inmuebles", "utilizados", "administracion", "comunidad", "oeste", "este",
    "norte", "especiales", "especial",
}

# Mirrors v_peers / v_rate_lots in spine/views.sql: open procedures with a published bid count.
RATE_LOTS = "is_open and n_bids is not null"
FRAMEWORK_RE = re.compile(
    r"deriva\w*\s+(del?\s+)?(a\.?\s?m\.?|acuerdo\s+marco)|basad[oa]\s+(en\s+(el\s+)?)?acuerdo\s+marco"
    r"|\bAM\s+[A-Z]*\s*\d|acuerdo\s+marco", re.IGNORECASE)
EXTENSION_RE = re.compile(r"^\s*pr[oó]rroga", re.IGNORECASE)
PERSON_NIF_RE = r"^([0-9]{8}|[XYZ][0-9]{7})[A-Z]$"     # DNI / NIE: an individual, not a company
NIF_SQL = "regexp_replace(regexp_replace(upper(winner_nif), '[^A-Z0-9]', '', 'g'), '^ES', '')"


def _alnum(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def is_framework_derived(title: str | None) -> bool:
    return bool(FRAMEWORK_RE.search(title or ""))


def is_extension(title: str | None) -> bool:
    return bool(EXTENSION_RE.search(title or ""))


def incumbent_is_manufacturer(title: str | None, winner: str | None) -> bool:
    """The winner's leading name token appears in the title (e.g. 'Mantenimiento ... Dräger' won by Dräger)."""
    words = [w for w in re.split(r"[\s,.]+", winner or "") if len(_alnum(w)) >= 4]
    title_words = {_alnum(w) for w in re.split(r"[\s,.()]+", title or "")}
    return bool(words) and _alnum(words[0]) in title_words


def topic_terms(con, title: str | None, exclude: str | None = None) -> list[str]:
    """The title's rarest meaningful words (accent-free), skipping the incumbent's own name."""
    skip = set(normalise(exclude).split())
    words = sorted({w for w in normalise(title).split() if len(w) >= 5 and w not in skip | TOPIC_STOP})
    if not words:
        return []
    total, *dfs = con.execute(
        "select count(*), " + ", ".join("count(*) filter (where strip_accents(lower(title)) like ?)" for _ in words)
        + " from lots where is_health", [f"%{w}%" for w in words]).fetchone()
    ok = [(df, w) for df, w in zip(dfs, words, strict=True) if TOPIC_MIN_DF <= df <= TOPIC_MAX_SHARE * total]
    return [w for _, w in sorted(ok)[:TOPIC_CANDIDATES]]


def topic_sql(terms: list[str]) -> tuple[str, list[str]]:
    return " and ".join("strip_accents(lower(title)) like ?" for _ in terms), [f"%{t}%" for t in terms]


def _rate(row) -> dict | None:
    n, single, med_bids, med_disc = row
    if not n:
        return None
    return {"n": int(n), "single_bid_rate": single, "median_bids": med_bids, "median_discount": med_disc}


def contract(con, hospital: str, folder_id: str) -> dict:
    df = con.execute("select * from radar where hospital = ? and folder_id = ?", [hospital, folder_id]).df()
    if df.empty:
        raise KeyError(f"{folder_id} not on the {hospital} radar")
    r = df.iloc[0].to_dict()
    lots = con.execute("""
        select lot_id, n_bids, round(discount, 4) as discount, lot_budget, award_net, winner, band, cpv4
        from lots where buyer_nif = ? and folder_id = ? order by lot_budget desc nulls last""",
        [r["buyer_nif"], folder_id]).df()
    main = lots.iloc[0] if not lots.empty else None
    return {
        "folder_id": folder_id, "title": r["title"], "link": r["link"], "cpvs": r["cpvs"],
        "cpv2": r["cpv2"], "cpv4": None if main is None else main["cpv4"],
        "band": None if main is None else main["band"],
        "procedure": r["procedure"], "contract_type": r["contract_type"], "n_lots": int(r["n_lots"] or 0),
        "award_net": r["award_net"], "annual_budget": r["annual_budget"], "winner": r["winner"],
        "min_bids": r["min_bids"], "max_bids": r["max_bids"],
        "median_bids": None if lots.n_bids.dropna().empty else float(lots.n_bids.median()),
        "end_initial": _iso(r["end_initial"]), "end_max": _iso(r["end_max"]), "start_by": _iso(r["start_by"]),
        "lead_days": int(r["lead_days"]), "prep_days": int(r["prep_days"]), "flag": r["flag"],
        "successor_status": r["successor_status"], "brand_in_title": bool(r["brand_in_title"]),
        "months_to_end": None if r.get("months_to_end") is None or pd.isna(r.get("months_to_end")) else float(r["months_to_end"]),
        "truth": {k: _iso(r.get(f"truth_{k}")) if k == "recorded_on" else r.get(f"truth_{k}")
                  for k in ("status", "source", "recorded_on")} if r.get("truth_status") else None,
        "framework_derived": is_framework_derived(r["title"]), "is_extension": is_extension(r["title"]),
        "incumbent_is_manufacturer": incumbent_is_manufacturer(r["title"], r["winner"]),
        "lots": json.loads(lots.to_json(orient="records")),
        "_buyer_nif": r["buyer_nif"],
    }


def peer_benchmark(con, cpv4: str | None, band: str | None, cpv2: str | None = None,
                   terms: list[str] | None = None) -> dict | None:
    """National health peers. `code`: same CPV-4 and value band (all bands when thin). `topic`: same CPV-2 and
    the title's distinctive words, narrowing to the rarest word alone when the pair is too thin."""
    out = {"code": None, "topic": None}
    if cpv4 and band:
        row = con.execute("select n, single_bid_rate, median_bids, median_discount from peers "
                          "where cpv4 = ? and band = ?", [cpv4, band]).fetchone()
        if row and row[0] >= MIN_PEER_N:
            out["code"] = {"scope": f"CPV {cpv4}, band {band}", "cpv": cpv4, "band": band, **_rate(row)}
    if cpv4 and out["code"] is None:
        row = con.execute(f"""select count(*), avg((n_bids = 1)::int), median(n_bids), median(discount)
                              from lots where {RATE_LOTS} and is_health and award_d >= date '2017-01-01'
                                and cpv4 = ?""", [cpv4]).fetchone()
        r = _rate(row)
        out["code"] = ({"scope": f"CPV {cpv4}, all bands", "cpv": cpv4, "band": None, **r}
                       if r and r["n"] >= MIN_PEER_N else None)
    # the two rarest words together first, then each word alone from rarest to commonest
    for t in ([terms[:2]] if len(terms or []) > 1 else []) + [[w] for w in terms or []]:
        where, params = topic_sql(t)
        row = con.execute(f"""select count(*), avg((n_bids = 1)::int), median(n_bids), median(discount)
                              from lots where {RATE_LOTS} and is_health and award_d >= date '2017-01-01'
                                and cpv2 = ? and {where}""", [cpv2, *params]).fetchone()
        r = _rate(row)
        if r and r["n"] >= MIN_TOPIC_N:
            out["topic"] = {"scope": f"CPV {cpv2}, title has: {', '.join(t)}", "cpv": cpv2, "terms": t,
                            "small_sample": r["n"] < MIN_PEER_N, **r}
            break
    return out if (out["code"] or out["topic"]) else None


def hospital_history(con, buyer_nif: str, cpv4: str | None, as_of: date) -> dict | None:
    if not cpv4:
        return None
    since = as_of - timedelta(days=365 * HISTORY_YEARS)
    row = con.execute(f"""select count(*), avg((n_bids = 1)::int), median(n_bids), median(discount)
                          from lots where {RATE_LOTS} and buyer_nif = ? and cpv4 = ? and award_d >= ?""",
                      [buyer_nif, cpv4, since]).fetchone()
    out = _rate(row)
    return {"scope": f"own lots, CPV {cpv4}, since {since.isoformat()}", **out} if out else None


def bidder_value(con, cpv2: str | None, median_bids: float | None, annual_budget: float | None) -> dict | None:
    """Gain from one more bidder at this contract's typical bid count, CPV-2 curve, national pool as fallback."""
    if median_bids is None:
        return None
    k = max(1, round(median_bids))
    if k > MAX_K:
        return None
    for scope in (cpv2, "**"):
        row = con.execute("select n_k, n_k1, median_k, median_k1, delta, ci_lo, ci_hi from bidder_value "
                          "where cpv2 = ? and k = ?", [scope, k]).fetchone()
        if row:
            n_k, n_k1, m_k, m_k1, delta, lo, hi = row
            significant = lo > 0
            eur = delta * annual_budget if (significant and annual_budget) else None
            return {"curve": "national" if scope == "**" else f"CPV {scope}", "k": k, "n_k": n_k, "n_k1": n_k1,
                    "median_discount_k": m_k, "median_discount_k1": m_k1, "delta": delta,
                    "ci90": [lo, hi], "significant": significant,
                    "eur_per_year": None if eur is None else round(eur, -2),
                    "eur_per_year_ci90": None if eur is None else [round(lo * annual_budget, -2),
                                                                  round(hi * annual_budget, -2)],
                    "caveat": "association across past tenders, not a causal estimate"}
    return None


def invite_list(con, buyer_nif: str, cpv4: str | None, as_of: date, limit: int = INVITE_LIMIT,
                cpv2: str | None = None, terms: list[str] | None = None,
                region_nuts: str | None = None) -> list[dict]:
    """Companies winning this topic (CPV-2 + title words) or else this CPV-4 at other health buyers,
    never at this hospital for the same scope. Wins inside the profile's region (NUTS prefix) rank first."""
    nuts = load_profile().region.nuts_prefix if region_nuts is None else region_nuts
    if terms and cpv2:
        where, params = topic_sql(terms)
        scope_sql, scope_params = f"cpv2 = ? and {where}", [cpv2, *params]
    elif cpv4:
        scope_sql, scope_params = "cpv4 = ?", [cpv4]
    else:
        return []
    since = as_of - timedelta(days=365 * HISTORY_YEARS)
    df = con.execute(f"""
        with w as (
          select {NIF_SQL} as nif, winner, buyer_nif, buyer_key, nuts, award_d, n_bids
          from lots where {scope_sql} and is_health and award_d >= ? and winner_nif is not null),
        here as (select distinct nif from w where buyer_nif = ?),
        anyhere as (select distinct {NIF_SQL} as nif from lots where buyer_nif = ? and winner_nif is not null)
        select nif, mode(winner) as name, count(*) as wins, count(distinct buyer_key) as buyers,
               sum((? <> '' and starts_with(coalesce(nuts, ''), ?))::int) as wins_in_region, max(award_d) as last_win,
               median(n_bids) as median_bids_where_won,
               bool_or(nif in (select nif from anyhere)) as supplies_hospital_other_categories
        from w
        where nif not in (select nif from here) and not regexp_matches(nif, '{PERSON_NIF_RE}')
        group by nif having count(*) >= 2
        order by wins_in_region desc, buyers desc, wins desc limit ?""",
        [*scope_params, since, buyer_nif, buyer_nif, nuts, nuts, limit]).df()
    df["last_win"] = df["last_win"].astype(str)
    return json.loads(df.to_json(orient="records"))


def _has_table(con, name: str) -> bool:
    return bool(con.execute("select count(*) from information_schema.tables where table_name = ?", [name]).fetchone()[0])


def reason_for(con, buyer_nif: str, folder_id: str) -> dict | None:
    """Single-bid reason bucket of the folder's main lot from the `reasons` table (schema 2.1), else None."""
    if not _has_table(con, "reasons"):
        return None
    row = con.execute("""select r.reason_bucket, r.reason_confidence, r.lever, r.signals
                         from reasons r join lots l on r.buyer_key = l.buyer_key and r.folder_id = l.folder_id
                              and r.lot_id is not distinct from l.lot_id
                         where l.buyer_nif = ? and r.folder_id = ? order by l.award_net desc nulls last limit 1""",
                      [buyer_nif, folder_id]).fetchone()
    if not row:
        return None
    return {"bucket": row[0], "confidence": row[1], "lever": row[2], "signals": row[3]}


def tier_for_cpv(cpv: str | None) -> int | None:
    """Care-continuity tier (1 stops care, 2 degrades it, 3 deferrable) from the CPV tier table, if shipped."""
    if not cpv:
        return None
    try:
        from licita.spine.benchmark import tier_for
    except ImportError:
        return None
    tier, _ = tier_for(cpv)
    return tier


def _iso(v) -> str | None:
    if v is None or (hasattr(v, "isoformat") is False) or str(v) in ("NaT", "None"):
        return None
    return v.date().isoformat() if hasattr(v, "date") else v.isoformat()


def build(con, hospital: str, folder_id: str) -> dict:
    run_id, as_of = con.execute("select run_id, as_of from meta").fetchone()
    through = con.execute("select max(updated)::date from folders where updated <= ?", [f"{as_of}Z"]).fetchone()[0]
    c = contract(con, hospital, folder_id)
    nif = c.pop("_buyer_nif")
    c["days_past_start_by"] = (as_of - date.fromisoformat(c["start_by"])).days if c["start_by"] else None
    c["reason"] = reason_for(con, nif, folder_id)
    c["continuity_tier"] = tier_for_cpv(c["cpv4"] or (c["cpvs"] or "")[:4])
    peers = peer_benchmark(con, c["cpv4"], c["band"], c["cpv2"], topic_terms(con, c["title"], c["winner"]))
    terms = (peers or {}).get("topic", {}) and peers["topic"]["terms"]
    return {
        "hospital": hospital, "hospital_name": load_profile().buyer(hospital).name,
        "provenance": {"spine_run_id": run_id, "as_of": as_of.isoformat(), "data_through": through.isoformat(),
                       "source": "PLACSP feeds 643 + 1044"},
        "contract": c,
        "peer_benchmark": peers,
        "hospital_history": hospital_history(con, nif, c["cpv4"], as_of),
        "bidder_value": None if c["framework_derived"] else
            bidder_value(con, c["cpv2"], c["median_bids"], c["annual_budget"]),
        "invite_list": invite_list(con, nif, c["cpv4"], as_of, cpv2=c["cpv2"], terms=terms or None),
    }


def safe_name(folder_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", folder_id)


def build_all(hospital: str, flags: tuple[str, ...], spine: Path = SPINE, out_dir: Path = OUT_DIR) -> list[dict]:
    con = duckdb.connect(str(spine), read_only=True)
    ids = [r[0] for r in con.execute(
        f"select folder_id from radar where hospital = ? and flag in ({','.join('?' * len(flags))}) "
        "order by annual_budget desc nulls last", [hospital, *flags]).fetchall()]
    dest = out_dir / hospital / "facts"
    dest.mkdir(parents=True, exist_ok=True)
    sheets = []
    for fid in ids:
        f = build(con, hospital, fid)
        (dest / f"{safe_name(fid)}.json").write_text(json.dumps(f, ensure_ascii=False, indent=1, default=str))
        sheets.append(f)
    return sheets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--buyer", "--hospital", dest="buyer", help="buyer key from the profile (default: the first)")
    ap.add_argument("--flags", default="start_now,due_6m")
    args = ap.parse_args(argv)
    buyer = load_profile().buyer(args.buyer).key
    sheets = build_all(buyer, tuple(args.flags.split(",")))
    for s in sheets:
        c, p, h, b = s["contract"], s["peer_benchmark"] or {}, s["hospital_history"], s["bidder_value"]
        peer = p.get("topic") or p.get("code")
        tags = [t for t, on in (("AM", c["framework_derived"]), ("OEM", c["incumbent_is_manufacturer"]),
                                ("PRORR", c["is_extension"])) if on]
        print(f"{c['flag']:9} bids={c['median_bids']} "
              f"peer1={'-' if not peer else format(peer['single_bid_rate'], '.0%'):>4} "
              f"own1={'-' if not h else format(h['single_bid_rate'], '.0%'):>4} "
              f"+1bid={(b or {}).get('eur_per_year') or '-'!s:>7} inv={len(s['invite_list']):2} "
              f"{','.join(tags):9} topic={','.join((p.get('topic') or {}).get('terms', [])):24} {c['title'][:50]}")
    print(f"{len(sheets)} fact sheets -> {OUT_DIR / buyer / 'facts'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
