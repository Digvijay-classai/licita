"""Bilingual UI strings and locale-aware formatting. Spanish is the default.

Every user-visible string lives in STRINGS with both languages; tests fail if one is missing.
Verbatim quotes from pliegos and rulings are never translated (they are evidence); in English
mode they keep the Spanish original and the memo adds an English gloss.
"""
from __future__ import annotations

from datetime import date

LANGS = ("es", "en")
DEFAULT_LANG = "es"

STRINGS: dict[str, dict[str, str]] = {
    # Navigation and chrome
    "nav.home": {"es": "Portada", "en": "Overview"},
    "nav.radar": {"es": "Radar", "en": "Radar"},
    "nav.memos": {"es": "Memorias", "en": "Memos"},
    "nav.simulator": {"es": "Simulador", "en": "Simulator"},
    "nav.results": {"es": "Resultados", "en": "Results"},
    "nav.chat": {"es": "Preguntar", "en": "Ask"},
    "nav.ops": {"es": "Operaciones", "en": "Operations"},
    "lang.label": {"es": "Idioma", "en": "Language"},
    "as_of": {"es": "Datos a fecha {date}", "en": "Data as of {date}"},
    "stale_banner": {
        "es": "Los datos tienen más de 30 días. Ejecute `make refresh` antes de la reunión.",
        "en": "Data is more than 30 days old. Run `make refresh` before the meeting.",
    },
    "demo_banner": {
        "es": "Demostración: hospitales, proveedores y contratos inventados. No se llama a ningún modelo.",
        "en": "Demo: invented hospitals, suppliers and contracts. No model is called.",
    },
    "export_pdf": {"es": "Exportar PDF", "en": "Export PDF"},
    "memo_n_of": {"es": "Memoria {i} de {n}", "en": "Memo {i} of {n}"},

    # Radar statuses (glyph + label; colour is never the only signal)
    "status.start_now": {"es": "▲ Iniciar ya", "en": "▲ Start now"},
    "status.due_months": {"es": "◆ {m} meses", "en": "◆ {m} months"},
    "status.verify": {"es": "◆ Verificar sucesor", "en": "◆ Verify successor"},
    "status.replaced": {"es": "● Sustituido", "en": "● Replaced"},
    "status.replaced_partial": {"es": "◆ Sustituido en parte", "en": "◆ Partly replaced"},
    "status.framework": {"es": "● En acuerdo marco", "en": "● Under framework agreement"},
    "status.in_progress": {"es": "● Licitación en curso", "en": "● Re-tender in progress"},
    "status.extended": {"es": "● Prorrogado", "en": "● Extended"},
    "status.no_successor": {"es": "▲ Sin sucesor", "en": "▲ No successor"},
    "status.pending": {"es": "Pendiente", "en": "Pending"},
    "status.lapsed": {"es": "◆ Vencido sin sucesor", "en": "◆ Lapsed, no successor"},

    # Memo sections
    "memo.expires_in": {"es": "Vence en {m} meses", "en": "Expires in {m} months"},
    "memo.typical_lead": {"es": "Plazo típico {m} meses", "en": "Typical lead time {m} months"},
    "memo.last_tender": {
        "es": "Última licitación: {n} oferta(s), baja {pct}",
        "en": "Last tender: {n} bid(s), discount {pct}",
    },
    "memo.plus_one": {"es": "+1 licitador ≈ {eur}/año", "en": "+1 bidder ≈ {eur}/year"},
    "memo.clauses": {"es": "Cláusulas a revisar", "en": "Clauses to review"},
    "memo.peers": {"es": "Cómo lo licitan otros hospitales", "en": "How other hospitals tender this"},
    "memo.invite": {
        "es": "Proveedores a considerar para la consulta preliminar de mercado",
        "en": "Suppliers to consider for the preliminary market consultation",
    },
    "memo.not_applicable": {"es": "No aplica", "en": "Not applicable"},
    "memo.gloss": {"es": "", "en": "English gloss: {text}"},

    # Risk labels
    "risk.high": {"es": "▲ Riesgo alto", "en": "▲ High risk"},
    "risk.medium": {"es": "◆ Riesgo medio", "en": "◆ Medium risk"},

    # Empty / error states (always explain why)
    "empty.peers": {
        "es": "Menos de 10 licitaciones comparables: benchmark no mostrado.",
        "en": "Fewer than 10 comparable tenders: benchmark not shown.",
    },
    "empty.clauses": {
        "es": "No se encontraron cláusulas con precedentes de anulación en el pliego publicado.",
        "en": "No clauses with annulment precedents were found in the published tender documents.",
    },
    "empty.suppliers": {
        "es": "No hay proveedores alternativos con historial en esta categoría. Considere dividir en lotes.",
        "en": "No alternative suppliers with a track record in this category. Consider splitting into lots.",
    },
    "empty.radar": {"es": "Sin contratos por vencer en los próximos 12 meses.", "en": "No contracts due in the next 12 months."},
    "error.section_unavailable": {
        "es": "Sección no disponible: {reason}",
        "en": "Section unavailable: {reason}",
    },
    "error.unreadable_pliego": {
        "es": "el pliego no se pudo leer (PDF escaneado sin texto)",
        "en": "the tender document could not be read (scanned PDF without text)",
    },
    "chat.offline": {"es": "Sin conexión: el chat no está disponible.", "en": "Offline: chat is unavailable."},
    "chat.too_few": {
        "es": "No hay datos suficientes (menos de 10 contratos) para responder con rigor.",
        "en": "Not enough data (fewer than 10 contracts) to answer reliably.",
    },

    # Simulator
    "sim.lots": {"es": "Lotes", "en": "Lots"},
    "sim.days": {"es": "Plazo de presentación (días)", "en": "Submission period (days)"},
    "sim.brand": {"es": "Marca en el pliego", "en": "Brand named in specs"},
    "sim.expected": {"es": "≈ {n} ofertas", "en": "≈ {n} bids"},
    "sim.interval": {"es": "intervalo 80 %: {lo}–{hi}", "en": "80% interval: {lo}–{hi}"},
    "sim.out_of_range": {"es": "fuera del rango histórico", "en": "outside historical range"},
    "sim.disclaimer": {
        "es": "Asociado históricamente con más ofertas; no implica causalidad.",
        "en": "Historically associated with more bids; does not imply causation.",
    },

    # App chrome
    "app.kicker": {"es": "Licita · apoyo a la contratación", "en": "Licita · procurement support"},
    "app.sources": {"es": "Fuentes", "en": "Sources"},
    "app.source_placsp": {
        "es": "Plataforma de Contratación del Sector Público (feeds 643 y 1044), lotes adjudicados por "
              "procedimiento abierto con nº de ofertas publicado.",
        "en": "Spanish Public Sector Procurement Platform (feeds 643 and 1044), lots awarded by open "
              "procedure with a published bid count.",
    },

    # Overview
    "home.curve_title": {
        "es": "Con una sola oferta, la baja mediana es {b1}; con cuatro, {b4}",
        "en": "With a single bid the median discount is {b1}; with four, {b4}",
    },
    "home.curve_sub": {
        "es": "Baja mediana sobre presupuesto según el nº de ofertas. Hospitales y servicios de salud de "
              "toda España, procedimiento abierto, {n} lotes.",
        "en": "Median discount on budget by number of bids. Hospitals and health services across Spain, "
              "open procedure, {n} lots.",
    },
    "home.axis_bids": {"es": "Ofertas recibidas", "en": "Bids received"},
    "home.axis_discount": {"es": "Baja mediana", "en": "Median discount"},
    "home.radar_title": {
        "es": "{n} contratos de {h} deberían empezar ya su relicitación",
        "en": "{n} {h} contracts should start re-tendering now",
    },
    "home.radar_sub": {
        "es": "Vencen antes de lo que tarda una licitación típica de su categoría, más 90 días de preparación.",
        "en": "They expire sooner than a typical tender in their category takes, plus 90 days of preparation.",
    },
    "home.single_bid_share": {
        "es": "{k} de ellos recibieron una sola oferta la última vez.",
        "en": "{k} of them received a single bid last time.",
    },

    # Radar table
    "radar.title": {
        "es": "{n} contratos requieren decisión en los próximos 6 meses",
        "en": "{n} contracts need a decision in the next 6 months",
    },
    "radar.col_contract": {"es": "Contrato", "en": "Contract"},
    "radar.col_ends": {"es": "Vence", "en": "Ends"},
    "radar.col_start_by": {"es": "Iniciar antes de", "en": "Start by"},
    "radar.col_bids": {"es": "Ofertas", "en": "Bids"},
    "radar.col_annual": {"es": "Presupuesto anual", "en": "Annual budget"},
    "radar.col_plus_one": {"es": "+1 licitador ≈ €/año", "en": "+1 bidder ≈ €/yr"},
    "radar.col_status": {"es": "Estado", "en": "Status"},
    "radar.none_due": {"es": "Sin contratos en este grupo.", "en": "No contracts in this group."},
    "radar.verify_note": {
        "es": "Vencieron en los últimos 12 meses y no se encuentra una nueva licitación publicada. Puede "
              "tratarse de una prestación que continúa sin contrato, de una compra centralizada o de un "
              "expediente aún no publicado.",
        "en": "They ended in the last 12 months and no new tender has been found. This may be a service "
              "continuing without a contract, a centralised purchase, or a file not yet published.",
    },
    "radar.lapsed_note": {
        "es": "Vencieron hace más de un año sin sucesor detectado. Histórico para confirmar con el servicio "
              "de contratación; no generan alerta.",
        "en": "They ended more than a year ago with no successor detected. History to confirm with the "
              "procurement team; they raise no alert.",
    },
    "data_through": {
        "es": "Datos de PLACSP hasta el {date} · radar calculado el {asof}",
        "en": "PLACSP data through {date} · radar computed on {asof}",
    },

    # Memo headlines (deterministic findings; the LLM narrative may extend, never contradict)
    "head.framework": {
        "es": "Derivado de un acuerdo marco: la competencia se decide en el acuerdo, no en el hospital",
        "en": "Derived from a framework agreement: competition is set in the framework, not at the hospital",
    },
    "head.exclusive": {
        "es": "Oferta única también en otros hospitales ({p}): probable exclusividad técnica",
        "en": "Single bid at other hospitals too ({p}): likely technical exclusivity",
    },
    "head.single_vs_peers": {
        "es": "Una sola oferta aquí; en contratos comparables solo ocurre en el {p}",
        "en": "A single bid here; in comparable contracts this happens only {p} of the time",
    },
    "head.single": {
        "es": "Una sola oferta en la última licitación",
        "en": "A single bid in the last tender",
    },
    "head.bids_vs_peers": {
        "es": "{n} ofertas en la última licitación; la mediana en contratos comparables es {m}",
        "en": "{n} bids in the last tender; the median in comparable contracts is {m}",
    },
    "memo.flag_extension": {
        "es": "Este expediente es una prórroga: la próxima licitación sustituye al contrato original.",
        "en": "This file is an extension: the next tender replaces the original contract.",
    },
    "memo.flag_oem": {
        "es": "El adjudicatario es el fabricante. La pregunta es si otras empresas pueden mantener este equipo.",
        "en": "The incumbent is the manufacturer. The question is whether other firms can service this equipment.",
    },
    "memo.flag_framework": {
        "es": "No se estima el valor de un licitador más: el contrato deriva de un acuerdo marco.",
        "en": "The value of one more bidder is not estimated: the contract derives from a framework agreement.",
    },
    "memo.plus_one_if": {
        "es": "Solo si existe un segundo proveedor capaz: en contratos comparables no lo hubo en el {p} de los "
              "casos. Si lo hubiera, ",
        "en": "Only if a second capable supplier exists: in comparable contracts there was none {p} of the time. "
              "If there were, ",
    },
    "memo.plus_one_ns": {
        "es": "Un licitador más no se asocia con una baja mayor en esta categoría (intervalo incluye 0).",
        "en": "One more bidder is not associated with a larger discount in this category (interval includes 0).",
    },
    "memo.plus_one_basis": {
        "es": "De {k} a {k1} ofertas, la baja mediana pasa de {a} a {b} ({curve}; IC 90 % {lo}–{hi}/año).",
        "en": "From {k} to {k1} bids, the median discount moves from {a} to {b} ({curve}; 90% CI {lo}–{hi}/yr).",
    },
    "memo.peers_title": {
        "es": "Cuántas veces se recibe una sola oferta",
        "en": "How often a single bid is received",
    },
    "memo.peer_here": {"es": "Este hospital (5 años)", "en": "This hospital (5 years)"},
    "memo.peer_topic": {"es": "Contratos comparables", "en": "Comparable contracts"},
    "memo.peer_code": {"es": "Mismo código CPV", "en": "Same CPV code"},
    "memo.small_sample": {"es": "muestra pequeña", "en": "small sample"},
    "memo.n_lots": {"es": "{n} lotes", "en": "{n} lots"},
    "memo.inv_wins": {"es": "Adjudicaciones", "en": "Awards"},
    "memo.inv_buyers": {"es": "Compradores", "en": "Buyers"},
    "memo.inv_region": {"es": "En {region}", "en": "In {region}"},
    "memo.inv_last": {"es": "Última", "en": "Latest"},
    "memo.inv_note": {
        "es": "Empresas que ganan contratos comparables en otros centros sanitarios y nunca en este hospital. "
              "Se excluyen personas físicas.",
        "en": "Companies that win comparable contracts at other health buyers and never at this hospital. "
              "Individuals are excluded.",
    },
    "memo.clauses_pending": {
        "es": "pendiente de la extracción de doctrina del {tribunal}",
        "en": "pending the {tribunal} doctrine extraction",
    },
    "memo.select": {"es": "Contrato", "en": "Contract"},
    # continuity first: the date is the finding, competition comes second
    "memo.cont_late": {
        "es": "El expediente debería haber empezado hace {d} días",
        "en": "The file should have started {d} days ago",
    },
    "memo.cont_due": {"es": "Quedan {d} días para iniciar el expediente", "en": "{d} days left to start the file"},
    "memo.cont_verify": {
        "es": "Plazo inicial vencido el {date} sin sucesor publicado: confirmar prórroga",
        "en": "Initial term ended on {date} with no successor published: confirm the extension",
    },
    "memo.cont_covered": {"es": "Sucesor localizado: {status}", "en": "Successor found: {status}"},
    "memo.cont_truth": {"es": "Confirmado por el equipo ({source}, {date}): {status}",
                        "en": "Confirmed by the team ({source}, {date}): {status}"},
    "memo.reason": {"es": "Por qué una sola oferta", "en": "Why one bid"},
    "memo.lever": {"es": "Palanca", "en": "Lever"},
    "memo.tier": {"es": "Criticidad asistencial: nivel {t}", "en": "Care-continuity tier {t}"},
    "reason.structural": {"es": "Estructural: poca palanca en el pliego", "en": "Structural: little leverage in the tender"},
    "reason.earlier_decision": {"es": "Decisión anterior o continuidad clínica", "en": "Earlier decision or clinical continuity"},
    "reason.addressable": {"es": "Abordable en el próximo pliego", "en": "Addressable in the next tender"},
    "reason.confidence": {"es": "confianza {c}", "en": "confidence {c}"},
    "nav.region": {"es": "Región", "en": "Region"},
    "region.title": {
        "es": "{n} de {m} compradores tienen contratos con el plazo inicial vencido sin sucesor publicado",
        "en": "{n} of {m} health authorities have contracts past their initial term with no successor published",
    },
    "region.sub": {
        "es": "Comparación dentro de la región, con el servicio de salud como una fila más. Bandas, no puestos: "
              "«limitado por los datos» significa que el comprador publica por la plataforma regional.",
        "en": "Comparison within the region, with the health service as one more row. Bands, not ranks: "
              "“data-limited” means the buyer publishes through the regional platform.",
    },
    "region.col_buyer": {"es": "Comprador", "en": "Buyer"},
    "region.col_on_time": {"es": "Expedientes a tiempo", "en": "Files on time"},
    "region.col_past_term": {"es": "Vencidos sin sucesor, €/año", "en": "Past term, no successor, €/yr"},
    "region.col_single": {"es": "Oferta única real · esperada", "en": "Single bid actual · expected"},
    "region.col_deserted": {"es": "Lotes desiertos", "en": "Deserted lots"},
    "region.col_coverage": {"es": "Cobertura", "en": "Coverage"},
    "band.on_track": {"es": "● en orden", "en": "● on track"},
    "band.watch": {"es": "◆ vigilar", "en": "◆ watch"},
    "band.behind": {"es": "▲ por detrás", "en": "▲ behind"},
    "band.data_limited": {"es": "○ limitado por los datos", "en": "○ data-limited"},
    "region.central_title": {
        "es": "Dónde conviene centralizar: el mismo objeto comprado por varios compradores al mismo proveedor",
        "en": "Where centralising pays: the same object bought by several authorities from the same supplier",
    },
    "region.col_object": {"es": "Objeto (CPV)", "en": "Object (CPV)"},
    "region.col_supplier": {"es": "Proveedor", "en": "Supplier"},
    "region.col_buyers": {"es": "Compradores", "en": "Buyers"},
    "region.col_value": {"es": "€ adjudicado", "en": "€ awarded"},
    "region.col_single_share": {"es": "Oferta única", "en": "Single bid"},
    "region.none": {"es": "La tabla regional no está en esta spine: `make build`.", "en": "No regional table in this spine: `make build`."},
    "scope.code": {"es": "CPV {cpv}, {band}", "en": "CPV {cpv}, {band}"},
    "scope.topic": {"es": "CPV {cpv} y título con «{terms}»", "en": "CPV {cpv} and title with “{terms}”"},
    "band.all": {"es": "todos los importes", "en": "all values"},
    "band.a<15k": {"es": "lotes de menos de 15.000 €", "en": "lots under €15,000"},
    "band.b<100k": {"es": "lotes de 15.000 a 100.000 €", "en": "lots of €15,000–100,000"},
    "band.c<500k": {"es": "lotes de 100.000 a 500.000 €", "en": "lots of €100,000–500,000"},
    "band.d500k+": {"es": "lotes de 500.000 € o más", "en": "lots of €500,000 or more"},

    # Simple simulator (curve-based; the bid-count model comes later)
    "sim.title": {
        "es": "Si se reciben {n} ofertas, la baja mediana esperada es {p}",
        "en": "With {n} bids, the expected median discount is {p}",
    },
    "sim.bids": {"es": "Ofertas", "en": "Bids"},
    "sim.saving": {
        "es": "≈ {eur}/año frente a la última licitación ({k} oferta/s)",
        "en": "≈ {eur}/year versus the last tender ({k} bid/s)",
    },
    "sim.basis": {
        "es": "Curva de la categoría CPV {cpv} (o nacional si no hay datos). Estimación, no promesa.",
        "en": "CPV {cpv} category curve (national when data is thin). Estimate, not a promise.",
    },
    "chat.pending": {
        "es": "El chat con citas se activa cuando el proyecto se conecta a un modelo de Mistral.",
        "en": "The cited chat is enabled once the project is connected to a Mistral model.",
    },

    # Footers / disclaimers
    "footer.public_data": {
        "es": "Datos públicos (PLACSP, {tribunal}). Herramienta de apoyo: no sustituye al informe jurídico.",
        "en": "Public data (PLACSP, {tribunal}). Decision support only: does not replace the legal report.",
    },
    "estimate": {"es": "Estimación, no promesa.", "en": "Estimate, not a promise."},
}


def t(key: str, lang: str = DEFAULT_LANG, **kwargs: object) -> str:
    """Translate `key` into `lang`, formatting `{placeholders}` from kwargs."""
    if lang not in LANGS:
        raise ValueError(f"unsupported language {lang!r}; expected one of {LANGS}")
    entry = STRINGS[key]  # KeyError on unknown keys is intentional: fail loudly in tests
    return entry[lang].format(**kwargs) if kwargs else entry[lang]


def _group(n: int, sep: str) -> str:
    return f"{n:,}".replace(",", sep)


def fmt_num(x: float, lang: str = DEFAULT_LANG, decimals: int = 0) -> str:
    """1234567.8 → es '1.234.568' / en '1,234,568' (decimals=0)."""
    neg = x < 0
    x = abs(x)
    whole = int(round(x)) if decimals == 0 else int(x)
    if decimals == 0:
        s = _group(whole, "." if lang == "es" else ",")
    else:
        frac = f"{x:.{decimals}f}".split(".")[1]
        whole = int(f"{x:.{decimals}f}".split(".")[0])
        s = _group(whole, "." if lang == "es" else ",") + ("," if lang == "es" else ".") + frac
    return f"-{s}" if neg else s


def fmt_eur(x: float, lang: str = DEFAULT_LANG, decimals: int = 0) -> str:
    """es '72.000 €' / en '€72,000'."""
    n = fmt_num(x, lang, decimals)
    return f"{n} €" if lang == "es" else f"€{n}"


def fmt_pct(x: float, lang: str = DEFAULT_LANG, decimals: int = 1) -> str:
    """x is a fraction: 0.047 → es '4,7 %' / en '4.7%'."""
    n = fmt_num(x * 100, lang, decimals)
    return f"{n} %" if lang == "es" else f"{n}%"


_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_date(d: date, lang: str = DEFAULT_LANG) -> str:
    """es '08/10/2026' / en '8 Oct 2026' (unambiguous for UK and US readers)."""
    return d.strftime("%d/%m/%Y") if lang == "es" else f"{d.day} {_MONTHS_EN[d.month - 1]} {d.year}"
