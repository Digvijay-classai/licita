"""Licita demo app: one build per hospital, Spanish by default with an English switch.

  make app BUYER=HOSP_A         (LICITA_PROFILE picks the deployment, LICITA_BUYER the hospital; no logins)
"""
from __future__ import annotations

import os
from datetime import date
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from licita.app import components as ui
from licita.app import data, theme
from licita.config import DEMO
from licita.errors import LicitaError
from licita.i18n import DEFAULT_LANG, fmt_date, fmt_eur, fmt_num, fmt_pct, t
from licita.profile import load_profile

try:
    data.check_spine()
except LicitaError as _e:
    st.error(f"{_e.problem}\n\n{_e.cause}\n\n**Fix:** {_e.fix}")
    st.stop()
PROFILE = load_profile()
BUYER = PROFILE.buyer(os.environ.get("LICITA_BUYER") or None)
HOSPITAL = BUYER.key
TRIBUNAL = PROFILE.tribunal.short
PAGES = ("nav.home", "nav.radar", "nav.region", "nav.memos", "nav.simulator", "nav.chat")
STALE_DAYS = 30

st.set_page_config(page_title="Licita", page_icon="▲", layout="wide", initial_sidebar_state="collapsed")
st.html(theme.CSS)


@st.cache_data
def _as_of() -> date:
    return data.as_of()


@st.cache_data
def _data_through() -> date:
    return data.data_through()


@st.cache_data
def _curve(cpv2: str = "**") -> pd.DataFrame:
    return data.curve(cpv2)


@st.cache_data
def _hospital_curve(h: str) -> pd.DataFrame:
    return data.hospital_curve(h)


@st.cache_data
def _radar(h: str) -> pd.DataFrame:
    return data.radar(h)


@st.cache_data
def _sheets(h: str) -> dict[str, dict]:
    return data.fact_sheets(h)


def finding(text: str, sub: str | None = None) -> None:
    st.html(f'<div class="lk-finding">{escape(text)}</div>' + (f'<p class="lk-sub">{escape(sub)}</p>' if sub else ""))


def margin(*notes: str) -> None:
    st.html("".join(ui.note(n) for n in notes))


def source_note(lang: str, extra: str = "") -> str:
    return f"<b>{escape(t('app.sources', lang))}.</b> {escape(t('app.source_placsp', lang))} {extra}"


# ---------------------------------------------------------------- header
# Widget labels and options must not depend on the language: a changed label makes Streamlit treat the
# widget as new and reset it, which swallowed the first click. Defaults live in session state instead.
st.session_state.setdefault("lang", DEFAULT_LANG)
st.session_state.setdefault("page", "nav.home")
lang = st.session_state["lang"] or DEFAULT_LANG
as_of = _as_of()
through = _data_through()
hname = BUYER.name
left, right = st.columns([6, 1], vertical_alignment="bottom")
with right:
    lang = st.segmented_control("lang", ["es", "en"], format_func=str.upper, key="lang",
                                label_visibility="collapsed") or DEFAULT_LANG
with left:
    st.html(f'<p class="lk-kicker">{escape(t("app.kicker", lang))} · {escape(hname)}</p>'
            f'<span class="lk-asof">{escape(t("data_through", lang, date=fmt_date(through, lang), asof=fmt_date(as_of, lang)))}</span>')
if DEMO:
    st.html(f'<div class="lk-flag">{escape(t("demo_banner", lang))}</div>')
elif (date.today() - through).days > STALE_DAYS:
    st.html(f'<div class="lk-flag">{escape(t("stale_banner", lang))}</div>')
page = st.segmented_control("nav", PAGES, format_func=lambda k: t(k, lang), key="page",
                            label_visibility="collapsed") or "nav.home"
template = theme.register(lang)


# ---------------------------------------------------------------- pages
def home() -> None:
    nat, own = _curve(), _hospital_curve(HOSPITAL)
    b = dict(zip(nat.bids, nat.median_discount, strict=True))
    main, side = st.columns([7, 2.4], gap="large")
    with main:
        finding(t("home.curve_title", lang, b1=fmt_pct(b[1], lang), b4=fmt_pct(b[4], lang)),
                t("home.curve_sub", lang, n=fmt_num(nat.n.sum(), lang)))
        fig = go.Figure()
        fig.add_scatter(x=nat.bids, y=nat.median_discount, mode="lines+markers", name="España",
                        line={"color": theme.PEER_GREY, "width": 2}, marker={"size": 8},
                        hovertemplate="%{x}: %{y:.1%}<extra></extra>")
        if not own.empty:
            fig.add_scatter(x=own.bids, y=own.median_discount, mode="markers", name=hname,
                            marker={"color": theme.ACCENT, "size": 11},
                            customdata=own.n, hovertemplate="%{x}: %{y:.1%} (n=%{customdata})<extra></extra>")
        fig.update_layout(template=template, height=320,
                          xaxis={"title": t("home.axis_bids", lang), "dtick": 1},
                          yaxis={"title": t("home.axis_discount", lang), "tickformat": ".0%"})
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    with side:
        st.html("<div style='height:120px'></div>")
        margin(f"<span style='color:{theme.ACCENT}'>●</span> {escape(hname)} &nbsp; "
               f"<span style='color:{theme.PEER_GREY}'>●</span> España",
               source_note(lang))

    st.html('<hr class="lk-rule">')
    sheets, radar = _sheets(HOSPITAL), _radar(HOSPITAL)
    now = radar[radar.flag == "start_now"]
    single = int((now.min_bids == 1).sum())
    main, side = st.columns([7, 2.4], gap="large")
    with main:
        finding(t("home.radar_title", lang, n=len(now), h=BUYER.short),
                t("home.radar_sub", lang) + " " + t("home.single_bid_share", lang, k=single))
        st.html(ui.radar_table(now.head(6), sheets, as_of, lang))
    with side:
        st.html("<div style='height:120px'></div>")
        margin(escape(t("estimate", lang)) + " " + escape(t("sim.disclaimer", lang)))


def radar_page() -> None:
    radar, sheets = _radar(HOSPITAL), _sheets(HOSPITAL)
    due = radar[radar.flag.isin(["start_now", "due_6m"])]
    finding(t("radar.title", lang, n=len(due)), t("home.radar_sub", lang))
    st.html(ui.radar_table(due, sheets, as_of, lang))
    for flag, label, note in (("verify", "status.verify", "radar.verify_note"),
                              ("lapsed", "status.lapsed", "radar.lapsed_note")):
        rows = radar[radar.flag == flag].sort_values("end_initial", ascending=False)
        if len(rows):
            with st.expander(f"{t(label, lang)} ({len(rows)})"):
                st.html(f'<p class="lk-sub">{escape(t(note, lang))}</p>')
                st.html(ui.radar_table(rows, sheets, as_of, lang))
    margin(source_note(lang))


@st.cache_data
def _benchmark() -> pd.DataFrame:
    return data.benchmark_region(PROFILE.region.nuts_prefix)


@st.cache_data
def _central() -> pd.DataFrame:
    return data.centralisation_candidates(PROFILE.region.nuts_prefix)


def region_page() -> None:
    bench = _benchmark()
    if bench.empty:
        st.html(f'<p class="lk-sub">{escape(t("region.none", lang))}</p>')
        return
    past = bench[bench.past_term_no_successor_eur_yr.fillna(0) > 0]
    main, side = st.columns([7, 2.4], gap="large")
    with main:
        finding(t("region.title", lang, n=len(past), m=len(bench)), t("region.sub", lang))
        st.html(ui.benchmark_table(bench, lang, highlight=BUYER.nif))
        st.html(f'<hr class="lk-rule"><div class="lk-finding" style="font-size:24px">'
                f'{escape(t("region.central_title", lang))}</div>')
        st.html(ui.centralisation_table(_central(), lang))
    with side:
        st.html("<div style='height:120px'></div>")
        margin(escape(t("estimate", lang)), source_note(lang))


def memo_page() -> None:
    sheets = _sheets(HOSPITAL)
    if not sheets:
        st.html(f'<p class="lk-sub">{escape(t("empty.radar", lang))}</p>')
        return
    ids = list(sheets)
    fid = st.selectbox(t("memo.select", lang), ids, key="memo_id",
                       format_func=lambda i: (sheets[i]["contract"]["title"] or i)[:110])
    s = sheets[fid]
    c, bv, peers = s["contract"], s["bidder_value"], s["peer_benchmark"] or {}
    label, cls = ui.status(c["flag"], c["start_by"], as_of, lang)
    st.html(f'<p class="lk-kicker"><span class="lk-id">{escape(fid)}</span> · '
            f'<span class="{cls}">{escape(label)}</span> · '
            f'{escape(t("memo_n_of", lang, i=ids.index(fid) + 1, n=len(ids)))}</p>')

    main, side = st.columns([7, 2.4], gap="large")
    with main:
        # 1. continuity: the date is the finding
        finding(ui.continuity_headline(c, as_of, lang), c["title"])
        reason = ui.reason_line(c, lang)
        if reason:
            st.html(f'<p class="lk-sub">{reason}</p>')
        if c.get("continuity_tier"):
            st.html(f'<p class="lk-sub">{escape(t("memo.tier", lang, t=c["continuity_tier"]))}</p>')
        # 2. competition
        st.html(f'<hr class="lk-rule"><div class="lk-finding" style="font-size:24px">{escape(ui.headline(s, lang))}</div>')
        exclusive = ui.likely_exclusive(s)
        # an exclusive-looking contract never gets the +1 figure as its headline number
        st.html(ui.chairs(c["median_bids"], None if exclusive else (bv or {}).get("eur_per_year"), lang,
                          expect=not c["framework_derived"]))
        if bv and bv["significant"]:
            lo, hi = bv["eur_per_year_ci90"]
            cond = (t("memo.plus_one_if", lang, p=fmt_pct(ui.peer_of(s)["single_bid_rate"], lang, 0))
                    + t("memo.plus_one", lang, eur=fmt_eur(bv["eur_per_year"], lang)) + ". ") if exclusive else ""
            basis = t("memo.plus_one_basis", lang, k=bv["k"], k1=bv["k"] + 1,
                      a=fmt_pct(bv["median_discount_k"], lang), b=fmt_pct(bv["median_discount_k1"], lang),
                      curve=bv["curve"], lo=fmt_eur(lo, lang), hi=fmt_eur(hi, lang))
            st.html(f'<p class="lk-sub">{escape(cond + basis)} {escape(t("estimate", lang))}</p>')
        elif bv:
            st.html(f'<p class="lk-sub">{escape(t("memo.plus_one_ns", lang))}</p>')
        for key, on in (("memo.flag_framework", c["framework_derived"]), ("memo.flag_extension", c["is_extension"]),
                        ("memo.flag_oem", c["incumbent_is_manufacturer"])):
            if on:
                st.html(f'<div class="lk-flag">{escape(t(key, lang))}</div>')

        st.html(f'<hr class="lk-rule"><div class="lk-finding" style="font-size:24px">'
                f'{escape(t("memo.peers", lang))}</div><p class="lk-sub">{escape(t("memo.peers_title", lang))}</p>')
        bars = [(t("memo.peer_here", lang), s["hospital_history"], theme.ACCENT),
                (t("memo.peer_topic", lang), peers.get("topic"), theme.PEER_GREY),
                (t("memo.peer_code", lang), peers.get("code"), theme.PEER_GREY)]
        bars = [(name, p, col) for name, p, col in bars if p]
        if bars:
            fig = go.Figure(go.Bar(
                y=[b[0] for b in bars][::-1], x=[b[1]["single_bid_rate"] for b in bars][::-1], orientation="h",
                marker_color=[b[2] for b in bars][::-1],
                text=[f'{fmt_pct(b[1]["single_bid_rate"], lang, 0)} · {t("memo.n_lots", lang, n=b[1]["n"])}'
                      for b in bars][::-1], textposition="outside", cliponaxis=False,
                hoverinfo="skip"))
            fig.update_layout(template=template, height=60 + 48 * len(bars),
                              xaxis={"tickformat": ".0%", "range": [0, 1.25], "showticklabels": False},
                              yaxis={"showgrid": False})
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        else:
            st.html(f'<p class="lk-sub">{escape(t("empty.peers", lang))}</p>')

        st.html(f'<hr class="lk-rule"><div class="lk-finding" style="font-size:24px">'
                f'{escape(t("memo.invite", lang))}</div>')
        if s["invite_list"]:
            st.html(ui.invite_table(s["invite_list"], lang))
        else:
            st.html(f'<p class="lk-sub">{escape(t("empty.suppliers", lang))}</p>')

        st.html(f'<hr class="lk-rule"><div class="lk-finding" style="font-size:24px">'
                f'{escape(t("memo.clauses", lang))}</div><p class="lk-sub">'
                f'{escape(t("error.section_unavailable", lang, reason=t("memo.clauses_pending", lang, tribunal=TRIBUNAL)))}</p>')
    with side:
        st.html("<div style='height:96px'></div>")
        notes = []
        if c["end_initial"]:
            notes.append(f'<b>{escape(t("radar.col_ends", lang))}:</b> '
                         f'<span class="lk-num">{fmt_date(date.fromisoformat(c["end_initial"]), lang)}</span><br>'
                         f'<b>{escape(t("radar.col_start_by", lang))}:</b> '
                         f'<span class="lk-num">{fmt_date(date.fromisoformat(c["start_by"]), lang)}</span><br>'
                         f'{escape(t("memo.typical_lead", lang, m=round(c["lead_days"] / 30.4)))}')
        if c["annual_budget"]:
            notes.append(f'<b>{escape(t("radar.col_annual", lang))}:</b> '
                         f'<span class="lk-num">{fmt_eur(c["annual_budget"], lang)}</span>')
        for name, p in ((t("memo.peer_topic", lang), peers.get("topic")), (t("memo.peer_code", lang), peers.get("code"))):
            if p:
                small = f' ({escape(t("memo.small_sample", lang))})' if p.get("small_sample") else ""
                notes.append(f"<b>{escape(name)}:</b> {escape(ui.scope_label(p, lang))}{small}")
        notes.append(escape(t("memo.inv_note", lang)))
        link = f' <a href="{escape(c["link"])}" target="_blank" rel="noopener">PLACSP</a>' if c.get("link") else ""
        notes.append(source_note(lang, f'<span class="lk-id">{escape(s["provenance"]["spine_run_id"])}</span>{link}'))
        margin(*notes)


def simulator_page() -> None:
    sheets = _sheets(HOSPITAL)
    usable = {k: v for k, v in sheets.items() if not v["contract"]["framework_derived"] and v["contract"]["annual_budget"]}
    if not usable:
        st.html(f'<p class="lk-sub">{escape(t("empty.radar", lang))}</p>')
        return
    fid = st.selectbox(t("memo.select", lang), list(usable), key="sim_id",
                       format_func=lambda i: (usable[i]["contract"]["title"] or i)[:110])
    c = usable[fid]["contract"]
    cur = _curve(c["cpv2"])
    cpv = c["cpv2"]
    if len(cur) < 3:
        cur, cpv = _curve(), "**"
    disc = dict(zip(cur.bids, cur.median_discount, strict=True))
    now = int(min(max(round(c["median_bids"] or 1), min(disc)), max(disc)))
    n = st.slider(t("sim.bids", lang), min(disc), max(disc), now, key=f"sim_{fid}")
    gain = (disc.get(n, 0) - disc.get(now, 0)) * c["annual_budget"]
    main, side = st.columns([7, 2.4], gap="large")
    with main:
        finding(t("sim.title", lang, n=n, p=fmt_pct(disc[n], lang)), c["title"])
        dots = "".join('<span class="lk-dot"></span>' for _ in range(n))
        st.html(f'<div class="lk-chairs">{dots}<span class="lk-plus">'
                f'{escape(t("sim.saving", lang, eur=fmt_eur(round(gain, -2), lang), k=now))}</span></div>')
    with side:
        margin(escape(t("sim.basis", lang, cpv=cpv if cpv != "**" else "—")),
               escape(t("sim.disclaimer", lang)), source_note(lang))


def chat_page() -> None:
    st.html(f'<p class="lk-sub">{escape(t("chat.pending", lang))}</p>')


{"nav.home": home, "nav.radar": radar_page, "nav.region": region_page, "nav.memos": memo_page,
 "nav.simulator": simulator_page, "nav.chat": chat_page}[page]()
st.html(f'<div class="lk-footer">{escape(t("footer.public_data", lang, tribunal=TRIBUNAL))} · '
        f'{escape(t("as_of", lang, date=fmt_date(through, lang)))}</div>')
