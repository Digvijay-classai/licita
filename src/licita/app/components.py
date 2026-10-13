"""Pure HTML builders for the editorial layout (testable without Streamlit).

All text from data sources (titles, supplier names, ids) is HTML-escaped: PLACSP fields are untrusted.
"""
from __future__ import annotations

import math
from datetime import date
from html import escape

import pandas as pd

from licita.i18n import fmt_date, fmt_eur, fmt_num, fmt_pct, t
from licita.profile import load_profile

MAX_DOTS = 8


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v)) and v is not pd.NaT


def chairs(bids: float | None, eur: float | None, lang: str, small: bool = False, expect: bool = True) -> str:
    """La silla vacía: one ink dot per bid received, one dashed accent circle for the expected extra bidder.
    `expect=False` (e.g. framework-derived contracts) drops the empty chair: no extra bidder is expected."""
    sm = " sm" if small else ""
    n = max(0, min(MAX_DOTS, round(bids))) if _ok(bids) else 0
    dots = "".join(f'<span class="lk-dot{sm}"></span>' for _ in range(n))
    if expect:
        dots += f'<span class="lk-dot lk-empty{sm}" title="{escape(t("estimate", lang))}"></span>'
    plus =(f'<span class="lk-plus{sm}">{escape(t("memo.plus_one", lang, eur=fmt_eur(eur, lang)))}</span>'
            if _ok(eur) else "")
    return f'<div class="lk-chairs">{dots}{plus}</div>'


def months_between(a: date, b: date) -> int:
    return max(0, round((b - a).days / 30.4))


def status(flag: str, start_by, as_of: date, lang: str) -> tuple[str, str]:
    """(label, css class); the glyph always carries the meaning, colour only reinforces it."""
    if flag == "start_now":
        return t("status.start_now", lang), "lk-risk"
    if flag == "due_6m" and _ok(start_by):
        return t("status.due_months", lang, m=months_between(as_of, pd.Timestamp(start_by).date())), "lk-warn"
    if flag == "verify":
        return t("status.verify", lang), "lk-warn"
    if flag == "lapsed":
        return t("status.lapsed", lang), "lk-warn"
    return t("status.pending", lang), ""


def scope_label(p: dict, lang: str) -> str:
    """Peer-group description from structured fields, in the reader's language."""
    if p.get("terms"):
        return t("scope.topic", lang, cpv=p["cpv"], terms=", ".join(p["terms"]))
    return t("scope.code", lang, cpv=p["cpv"], band=t(f"band.{p['band']}" if p.get("band") else "band.all", lang))


def peer_of(sheet: dict) -> dict | None:
    p = sheet.get("peer_benchmark") or {}
    return p.get("topic") or p.get("code")


EXCLUSIVE_RATE = 0.8


def likely_exclusive(sheet: dict) -> bool:
    """Single bid here and in >= 80% of comparable contracts: competition is probably not available."""
    bids, peer = sheet["contract"].get("median_bids"), peer_of(sheet)
    return bool(_ok(bids) and round(bids) == 1 and peer and peer["single_bid_rate"] >= EXCLUSIVE_RATE)


def headline(sheet: dict, lang: str) -> str:
    """The memo's finding as one sentence, decided by rules on the fact sheet (never by the LLM)."""
    c, peer = sheet["contract"], peer_of(sheet)
    bids = c.get("median_bids")
    if c["framework_derived"]:
        return t("head.framework", lang)
    if _ok(bids) and round(bids) == 1:
        if likely_exclusive(sheet):
            return t("head.exclusive", lang, p=fmt_pct(peer["single_bid_rate"], lang, 0))
        if peer and peer["single_bid_rate"] <= 0.5:
            return t("head.single_vs_peers", lang, p=fmt_pct(peer["single_bid_rate"], lang, 0))
        return t("head.single", lang)
    if _ok(bids) and peer:
        return t("head.bids_vs_peers", lang, n=round(bids), m=fmt_num(peer["median_bids"], lang))
    return c["title"]


def continuity_headline(c: dict, as_of: date, lang: str) -> str:
    """The memo's first line: the calendar, before any competition figure. Rules on the fact sheet only."""
    truth = c.get("truth") or {}
    if truth.get("status"):
        return t("memo.cont_truth", lang, source=truth.get("source") or "", date=truth.get("recorded_on") or "",
                 status=truth["status"])
    flag, start_by, end = c.get("flag"), c.get("start_by"), c.get("end_initial")
    days = (as_of - date.fromisoformat(start_by)).days if start_by else None
    if flag == "start_now" and days is not None and days >= 0:
        return t("memo.cont_late", lang, d=days)
    if flag == "due_6m" and days is not None:
        return t("memo.cont_due", lang, d=-days)
    if flag in ("verify", "lapsed") and end:
        return t("memo.cont_verify", lang, date=fmt_date(date.fromisoformat(end), lang))
    if flag == "covered":
        return t("memo.cont_covered", lang, status=c.get("successor_status") or "")
    return c.get("title") or ""


def reason_line(c: dict, lang: str) -> str | None:
    """'Why one bid' as one escaped HTML line from the reason bucket on the fact sheet, or None."""
    r = c.get("reason") or {}
    if not r.get("bucket"):
        return None
    conf = f' · {escape(t("reason.confidence", lang, c=fmt_pct(r["confidence"], lang, 0)))}' if _ok(r.get("confidence")) else ""
    lever = f' · <b>{escape(t("memo.lever", lang))}:</b> {escape(r["lever"])}' if r.get("lever") else ""
    return (f'<b>{escape(t("memo.reason", lang))}:</b> {escape(t("reason." + r["bucket"], lang))}'
            f'{conf}{lever}')


FLAG_ORDER = {"start_now": 0, "verify": 1, "lapsed": 2, "due_6m": 3, "ok": 4, "covered": 5, "one_off": 6}


def continuity_key(sheet: dict) -> tuple:
    """Sort key for fact sheets: continuity first (late files, then past-term), framework-derived last."""
    c = sheet["contract"]
    return (bool(c.get("framework_derived")), FLAG_ORDER.get(c.get("flag"), 9),
            -(c.get("days_past_start_by") or 0), -(c.get("annual_budget") or 0))


def radar_table(df: pd.DataFrame, sheets: dict[str, dict], as_of: date, lang: str) -> str:
    if df.empty:
        return f'<p class="lk-sub">{escape(t("radar.none_due", lang))}</p>'
    head = "".join(f"<th>{escape(t(k, lang))}</th>" for k in (
        "radar.col_contract", "radar.col_ends", "radar.col_start_by", "radar.col_bids",
        "radar.col_plus_one", "radar.col_annual", "radar.col_status"))
    rows = []
    for r in df.itertuples():
        sheet = sheets.get(r.folder_id)
        bv = (sheet or {}).get("bidder_value") or {}
        eur = None if (sheet and likely_exclusive(sheet)) else bv.get("eur_per_year")
        label, cls = status(r.flag, r.start_by, as_of, lang)
        end = fmt_date(pd.Timestamp(r.end_initial).date(), lang) if _ok(r.end_initial) else "–"
        start = fmt_date(pd.Timestamp(r.start_by).date(), lang) if _ok(r.start_by) else "–"
        annual = fmt_eur(r.annual_budget, lang) if _ok(r.annual_budget) else "–"
        rows.append(
            f"<tr><td>{escape(r.title or '')}<br><span class='lk-id'>{escape(r.folder_id)}</span></td>"
            f"<td class='d'>{end}</td><td class='d'>{start}</td>"
            f"<td>{chairs(r.min_bids, None, lang, small=True, expect=bool(bv))}</td>"
            f"<td class='n' style='color:var(--accent)'>{fmt_eur(eur, lang) if _ok(eur) else '–'}</td>"
            f"<td class='n'>{annual}</td><td class='{cls}'>{escape(label)}</td></tr>")
    return f"<table class='lk-table'><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def band(value: str | None, lang: str) -> str:
    """Band label with its glyph; the glyph carries the meaning, never colour alone."""
    key = f"band.{value}" if value in ("on_track", "watch", "behind", "data_limited") else "band.data_limited"
    cls = {"on_track": "", "watch": "lk-warn", "behind": "lk-risk"}.get(value or "", "lk-id")
    return f'<span class="{cls}">{escape(t(key, lang))}</span>'


def benchmark_table(df: pd.DataFrame, lang: str, highlight: str | None = None) -> str:
    """One row per health authority of the region: timeliness, value past term, single bid vs expected,
    deserted lots, coverage. Bands, never ranks; the reader's own hospital is marked."""
    if df.empty:
        return f'<p class="lk-sub">{escape(t("region.none", lang))}</p>'
    head = "".join(f"<th>{escape(t(k, lang))}</th>" for k in (
        "region.col_buyer", "region.col_on_time", "region.col_past_term", "region.col_single",
        "region.col_deserted", "region.col_coverage"))
    rows = []
    for r in df.itertuples():
        me = " style='font-weight:600'" if highlight and r.buyer_key == highlight else ""
        on_time = fmt_pct(r.on_time_share, lang, 0) if _ok(r.on_time_share) else "–"
        past = fmt_eur(r.past_term_no_successor_eur_yr, lang) if _ok(r.past_term_no_successor_eur_yr) else "–"
        single = (f"{fmt_pct(r.single_bid_rate, lang, 0)} · {fmt_pct(r.expected_rate, lang, 0)}"
                  if _ok(r.single_bid_rate) and _ok(r.expected_rate) else "–")
        des = fmt_pct(r.deserted_rate, lang, 1) if _ok(r.deserted_rate) else "–"
        cov = fmt_pct(r.coverage_score, lang, 0) if _ok(r.coverage_score) else "–"
        rows.append(
            f"<tr{me}><td>{escape(r.buyer or '')}<br><span class='lk-id'>{escape(r.buyer_nif or r.buyer_key)}</span></td>"
            f"<td class='n'>{on_time} {band(r.band_timeliness, lang)}</td>"
            f"<td class='n'>{past}</td>"
            f"<td class='n'>{single} {band(r.band_competition, lang)}</td>"
            f"<td class='n'>{des} {band(r.band_deserted, lang)}</td>"
            f"<td class='n'>{cov}</td></tr>")
    return f"<table class='lk-table'><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def centralisation_table(df: pd.DataFrame, lang: str) -> str:
    if df.empty:
        return f'<p class="lk-sub">{escape(t("radar.none_due", lang))}</p>'
    head = "".join(f"<th>{escape(t(k, lang))}</th>" for k in (
        "region.col_object", "region.col_supplier", "region.col_buyers", "region.col_value", "region.col_single_share"))
    rows = "".join(
        f"<tr><td><span class='lk-id'>{escape(r.cpv4)}</span></td>"
        f"<td>{escape(r.supplier or '')}<br><span class='lk-id'>{escape(r.winner_nif)}</span></td>"
        f"<td class='n'>{fmt_num(r.n_buyers, lang)}</td><td class='n'>{fmt_eur(r.awarded_eur, lang)}</td>"
        f"<td class='n'>{fmt_pct(r.single_bid_share, lang, 0)}</td></tr>" for r in df.itertuples())
    return f"<table class='lk-table'><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def note(html_body: str) -> str:
    return f'<div class="lk-note">{html_body}</div>'


def invite_table(rows: list[dict], lang: str) -> str:
    head = "".join(f"<th>{escape(x)}</th>" for x in (
        "", t("memo.inv_wins", lang), t("memo.inv_buyers", lang), t("memo.inv_region", lang, region=load_profile().region.label),
        t("memo.inv_last", lang)))
    body = "".join(
        f"<tr><td>{escape(r['name'] or '')}<br><span class='lk-id'>{escape(r['nif'])}</span></td>"
        f"<td class='n'>{fmt_num(r['wins'], lang)}</td><td class='n'>{fmt_num(r['buyers'], lang)}</td>"
        f"<td class='n'>{fmt_num(r['wins_in_region'] or 0, lang)}</td>"
        f"<td class='d'>{fmt_date(date.fromisoformat(r['last_win'][:10]), lang)}</td></tr>" for r in rows)
    return f"<table class='lk-table'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
