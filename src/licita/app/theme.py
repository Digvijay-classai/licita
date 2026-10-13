"""Design tokens from DESIGN.md as CSS (injected once) and the single Plotly template."""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

FONTS = ("https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1"
         "&family=Atkinson+Hyperlegible+Next:wght@400;500;700&family=Geist:wght@400;500;600"
         "&family=IBM+Plex+Mono:wght@400;500&display=swap")

PAPER, SURFACE, INK, MUTED, HAIRLINE = "#FAF8F3", "#FFFFFF", "#1B1A17", "#6B665C", "#DDD6C8"
ACCENT, RISK, WARN, OK = "#1D3A8A", "#C2410C", "#B88A00", "#0F7B5F"
PEER_GREY = "#B5AE9F"

CSS = f"""
<style>
@import url('{FONTS}');
:root {{
  --paper:{PAPER}; --surface:{SURFACE}; --ink:{INK}; --muted:{MUTED}; --hairline:{HAIRLINE};
  --accent:{ACCENT}; --risk:{RISK}; --warn:{WARN}; --ok:{OK};
  --serif:'Instrument Serif', Georgia, serif; --sans:'Atkinson Hyperlegible Next', system-ui, sans-serif;
  --num:'Geist', system-ui, sans-serif; --mono:'IBM Plex Mono', ui-monospace, monospace;
}}
.stApp {{ background: var(--paper); color: var(--ink); }}
html, body, .stApp, .stMarkdown, p, li, label, button, input {{ font-family: var(--sans); }}
.block-container {{ max-width: 1050px; padding-top: 1.5rem; padding-bottom: 4rem; }}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.stMarkdown p, .stMarkdown li {{ font-size: 18px; line-height: 1.55; }}

.lk-kicker {{ font: 500 12px/1.4 var(--sans); letter-spacing: .14em; text-transform: uppercase;
             color: var(--muted); margin: 0 0 6px; }}
.lk-asof {{ font: 400 14px/1.4 var(--num); color: var(--muted); font-variant-numeric: tabular-nums; }}
.lk-finding {{ font: 400 34px/1.18 var(--serif); color: var(--ink); margin: 4px 0 8px; }}
.lk-sub {{ font: 400 15px/1.5 var(--sans); color: var(--muted); margin: 0 0 12px; }}
.lk-note {{ font: 400 14px/1.5 var(--sans); color: var(--muted); border-top: 1px solid var(--hairline);
           padding-top: 8px; margin-bottom: 16px; }}
.lk-note b {{ color: var(--ink); font-weight: 500; }}
.lk-rule {{ border: 0; border-top: 1px solid var(--hairline); margin: 32px 0 24px; }}
.lk-id {{ font-family: var(--mono); font-size: 13px; color: var(--muted); }}
.lk-num {{ font-family: var(--num); font-variant-numeric: tabular-nums; }}
.lk-flag {{ font: 400 15px/1.5 var(--sans); border-left: 2px solid var(--warn); padding: 2px 0 2px 12px;
           margin: 8px 0; }}

.lk-chairs {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 12px 0 4px; }}
.lk-dot {{ width: 14px; height: 14px; border-radius: 50%; background: var(--ink); display: inline-block; }}
.lk-dot.lk-empty {{ background: transparent; border: 1.5px dashed var(--accent); }}
.lk-dot.sm {{ width: 9px; height: 9px; }}
.lk-plus {{ font: 400 28px/1.2 var(--serif); color: var(--accent); margin-left: 10px; }}
.lk-plus.sm {{ font-size: 18px; margin-left: 6px; }}

.lk-table {{ width: 100%; border-collapse: collapse; font-size: 15px; }}
.lk-table th {{ font: 500 12px/1.3 var(--sans); letter-spacing: .08em; text-transform: uppercase;
               color: var(--muted); text-align: left; border-bottom: 1px solid var(--ink); padding: 6px 8px; }}
.lk-table td {{ border-bottom: 1px solid var(--hairline); padding: 10px 8px; vertical-align: top; }}
.lk-table td.n {{ font-family: var(--num); font-variant-numeric: tabular-nums; text-align: right;
                 white-space: nowrap; }}
.lk-table td.d {{ font-family: var(--num); font-variant-numeric: tabular-nums; white-space: nowrap; }}
.lk-risk {{ color: var(--risk); white-space: nowrap; }}
.lk-warn {{ color: var(--warn); white-space: nowrap; }}
.lk-ok {{ color: var(--ok); white-space: nowrap; }}
.lk-footer {{ font: 400 14px/1.5 var(--sans); color: var(--muted); border-top: 1px solid var(--hairline);
             margin-top: 48px; padding-top: 12px; }}
@media (max-width: 1000px) {{ .lk-finding {{ font-size: 28px; }} }}
</style>
"""


def plotly_template(lang: str) -> go.layout.Template:
    t = go.layout.Template()
    t.layout = go.Layout(
        font={"family": "Geist, system-ui, sans-serif", "size": 14, "color": INK},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        separators=",." if lang == "es" else ".,",
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        xaxis={"showgrid": False, "linecolor": HAIRLINE, "ticks": "", "zeroline": False},
        yaxis={"gridcolor": HAIRLINE, "zeroline": False, "ticks": ""},
        showlegend=False, hoverlabel={"font": {"family": "Geist, system-ui, sans-serif"}},
        colorway=[PEER_GREY, ACCENT],
    )
    return t


def register(lang: str) -> str:
    name = f"licita_{lang}"
    pio.templates[name] = plotly_template(lang)
    return name
