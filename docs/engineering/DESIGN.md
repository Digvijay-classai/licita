# Design System — Licita

## Product Context
- **What this is:** Spanish-language decision support for public-hospital procurement teams.
  It covers the contracts that need re-tendering, competition memos (peer benchmark, value of one
  more bidder, at-risk clauses with tribunal citations, suppliers to invite), a what-if simulator and
  a cited chat.
- **Who it's for:** the management and procurement team of a public hospital, shown live on a laptop or
  projector. Secondary audience: technical evaluators reading the code and the evidence.
- **Space/industry:** public procurement analytics, health. Peers: Gobierto Contratación
  (SaaS-style), Civio (civic data journalism), Our World in Data (the clarity benchmark).
- **Project type:** analytical web app (Streamlit + Plotly), plus a printable 4-page PDF brief.
- **Memorable thing:** "This is beautifully clear."

## Aesthetic Direction
- **Direction:** Editorial brief, "expediente anotado" (the annotated case file). A typeset public
  dossier marked up by a good data journalist, not a dashboard.
- **Decoration level:** minimal. Hairline rules, margin notes and the highlighter underline do the work.
- **Mood:** calm, exact, authoritative. The first-3-seconds reaction is relief: "finally, someone
  just tells me plainly."
- **Rules:**
  - **Every chart title is the finding as a sentence.** Write "Con una sola oferta, la baja mediana es
    0 %", not "Baja por nº de ofertas".
  - **Every chart and every number has a source** (a source line or a margin note).
  - **Quote before verdict.** Show the verbatim clause first, then the ruling footnote, and the risk
    label last.
  - **No KPI tiles, no cards-as-layout, no sidebar chrome, no gradients, no sparkle/AI icons.**
- **Signature element, "la silla vacía" (the empty chair):** a row of filled ink dots (one per bid
  received) followed by one dashed circle in the accent colour (the expected extra bidder), with
  "+1 licitador ≈ X €/año" in the display serif.
  - Used on the radar, the memo verdict, the simulator (dots fill as sliders move) and the PDF cover.
  - The dashed circle always means *expected*, never *promised*. Copy must keep "≈" and "estimación".
- **Reference sites:** https://ourworldindata.org (sourcing, muted charts), https://civio.es
  (Spanish civic data voice), https://contratos.gobierto.es (what to avoid: SaaS gradient look).
- **Preview:** generated locally during the design review (not in the repository)

## Typography
- **Display/Hero:** Instrument Serif 400. Memo titles, chart findings, the "+1 licitador" figure.
  Editorial authority without stiffness.
- **Body / UI / labels:** Atkinson Hyperlegible Next (400/500/700). Designed for legibility, so it
  holds up on projectors and for low-vision readers.
- **Data/Tables:** Geist with `font-variant-numeric: tabular-nums`. Used for all numbers, € amounts,
  percentages, dates and Plotly tick labels.
- **Identifiers:** IBM Plex Mono 400. Expediente numbers, CPV codes and ruling ids only, never for
  prose or numbers.
- **Loading:** Google Fonts:
  `https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Atkinson+Hyperlegible+Next:wght@400;500;700&family=Geist:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap`.
  The PDF embeds the same fonts.
- **Scale (px):**
  - display 64
  - h1 / finding 34
  - h2 38 (section)
  - h3 24 (clause quote, italic serif)
  - body 18
  - small 15
  - note / margin 14
  - kicker 12 (uppercase, +0.14em tracking)

## Color
- **Approach:** restrained. Ink, paper and one blue; colour is rare and therefore meaningful.
- **Base:** paper `#FAF8F3` · surface `#FFFFFF` · ink `#1B1A17` · muted `#6B665C` · hairline `#DDD6C8`.
- **Accent:** ink blue `#1D3A8A`. Used **only** for "your hospital" in charts, citation links and
  margin-note numbers, and the empty chair.
- **Semantic, always paired with a glyph:**
  - risk ▲ `#C2410C`
  - warning / verify ◆ `#B88A00`
  - ok / replaced ● `#0F7B5F`
  - info: the accent
- **Highlighter** (clause quotes): `rgba(242,226,122,.6)`, applied as an underline band
  (bottom 42%).
- **Charts:** peers grey `#B5AE9F`, focus hospital accent. When categories are needed, use the
  Okabe-Ito palette: `#0072B2 #E69F00 #009E73 #CC79A7 #56B4E9`.
- **Dark mode:** paper `#16161A`, surface `#1E1E23`, ink `#ECE8DF`, muted `#A39E93`, hairline `#34343B`,
  accent `#8FA8EE`. Semantic colours are lifted for contrast (`#F0834D`, `#E0B83A`, `#4CC29A`).
  Light mode is the default for meetings and projectors.

## Spacing
- **Base unit:** 8px.
- **Density:** comfortable. Projector-readable, generous line height (1.55 body).
- **Scale:** 2xs(2) xs(4) sm(8) md(16) lg(24) xl(32) 2xl(48) 3xl(64).

## Layout
- **Approach:** hybrid editorial. A reading column of 700px, a 48px gap and a 240px **margin column**
  for citations and notes. Charts may span the reading column only (no full-bleed chrome).
- **Below 1000px:** the margin notes drop under their paragraph.
- **Max content width:** ~1050px.
- **Border radius:** sm 2px (tags), md 3px (buttons, inputs). Nothing rounder; this is paper, not
  bubbles.
- **Streamlit implementation:** wide layout plus custom CSS injected once (`licita/app/theme.py`).
  Memo bodies are rendered as HTML components using these tokens. A single Plotly template
  (`licita_template`) sets fonts, colours, gridlines (hairline), no chart borders and Spanish number
  formats (`separators=",."`).

## Motion
- **Approach:** minimal-functional. Only the empty-chair dots animate (fill/background, 250ms
  ease-out) when the simulator changes. No entrance animations.
- **Easing:** enter ease-out, exit ease-in, move ease-in-out. **Durations:** micro 80ms, short 200ms,
  medium 300ms.

## Formatting & language
- **Bilingual, Spanish by default.**
  - An `ES | EN` switch sits in the top-right of the chrome; the choice persists for the session.
  - The PDF follows the current language.
  - All strings live in `src/licita/i18n.py`, and tests fail if a language is missing.
- **Spanish:** plain administrative Spanish. Numbers `1.234.567 €`, `4,7 %` (space before %),
  dates `08/10/2026`.
- **English:** numbers `€1,234,567`, `4.7%`, dates `8 Oct 2026`.
- **Verbatim quotes are never translated.** Pliego clauses and ruling text are evidence: in English
  mode the Spanish original stays, followed by an "English gloss".
- **LLM-written narrative** (memo prose, chat answers) is generated in the selected language; memos
  are precomputed in both.
- Every screen header and PDF page shows **"Datos a fecha DD/MM/AAAA"**.
- **Status labels:** Iniciar ya ▲ · 6 meses ◆ · Verificar ◆ · Sustituido ● · Pendiente.
- **Empty states explain why** ("Menos de 10 licitaciones comparables: benchmark no mostrado").

## Print (PDF brief)
- The paper background turns white and margin notes become numbered footnotes.
- Only the semantic colours and the accent remain, and each is still legible in black and white
  because of the glyphs.
- Structure: cover (national finding + radar + empty chairs), then one memo per page.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-10-08 | Initial design system created | /design-consultation: research (OWID, Civio, Gobierto, gov dashboards) + outside design voice; memorable thing "beautifully clear" |
| 2026-10-08 | Editorial brief, no KPI tiles/cards | Hospital management wants a briefing, not a control room; differentiates from SaaS-style competitors |
| 2026-10-08 | "La silla vacía" signature glyph | Turns the product thesis (+1 bidder ≈ €) into a recognisable mark; from the outside design voice |
| 2026-10-08 | Paper lightened #F5F1E8 → #FAF8F3; numbers in Geist (not mono) | Warm paper turns yellow on projectors; monospace numbers read as a developer tool |
| 2026-10-08 | Bilingual ES/EN, Spanish default; quotes stay verbatim in Spanish | User request; procurement teams work in Spanish, evaluators read English |
| 2026-10-08 | National curve excludes 8+ bid lots | 8+ bucket (62.6% median discount) is unit-price or framework artefacts; the honest headline is 16.6% at 4 bids |
| 2026-10-09 | Headline figures from the full spine | Feeds 643 + 1044, 2017–2026: 0.3 % with one bid, 20.1 % with four (173,846 health lots) |
| 2026-10-09 | Likely-exclusive contracts never show the +1 € figure as a headline | Single bid here and in ≥ 80 % of comparables: the figure becomes a conditional sentence |
| 2026-10-09 | Headline corrected to 0.0 % with one bid, 13.8 % with four (140,952 lots) | Two data defects found on cross-check: lots in folders without a lot list were measured against the whole folder budget, and repeated award rows from overlapping archives; see DATA.md |
