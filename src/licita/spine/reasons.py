"""Why did this lot get one bid? A reason bucket per single-bid awarded lot, with the signals and the lever.

A single bid is not one problem. The memo must say different things depending on the cause, so every
open-procedure lot awarded with one bid is sorted into:

  structural        no clause in the next tender would change it: the manufacturer services its own
                    machine, the market is a monopoly everywhere (peer cell >= 80 % single bids), the
                    object is a patented medicine, the universal postal service, or exclusivity is stated
  earlier_decision  competition was decided before this tender: an extension, a framework call-off,
                    consumables or implants tied to installed equipment ("compatible con", "del equipo")
  addressable       nothing structural found; the sub-signals say which lever is worth trying (thin market,
                    short deadline, very large lot, a service market that is contested elsewhere)

Rules are deterministic and cheap; the confidence says how many independent signals agree (0.9 two, 0.7 one,
0.5 none). The procurement team's own labels on a sample of lots are the ground truth this is scored against
(see docs/engineering/EVALS.md once that session has happened). Levers are short Spanish phrases the memo can
print as they are.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import duckdb
import pandas as pd

from licita.memo.facts import incumbent_is_manufacturer, is_extension, is_framework_derived

PEER_MIN_N = 20              # a peer cell needs this many lots before its single-bid rate means anything
PEER_EXCLUSIVE = 0.8         # at or above: the market is one supplier everywhere, not just here
PEER_THIN = 0.5              # between this and PEER_EXCLUSIVE: a thin market worth a market consultation
PEER_COMPETITIVE = 0.5       # below: the same object draws several bids at other buyers
SHORT_DEADLINE_DAYS = 20
LARGE_LOT_EUR = 500_000
SERVICE_CPV2 = {"50", "55", "79", "90", "98"}

EXCLUSIVITY_RE = re.compile(r"exclusiv|[úu]nico proveedor|patente|licencia de uso", re.IGNORECASE)
COMPATIBILITY_RE = re.compile(
    r"compatible con|instalad[oa]s? en|del equipo|para el (sistema|equipo)|fungible .* sistema", re.IGNORECASE)
ACTIVE_PRINCIPLE_RE = re.compile(r"principio activo|\bdci\b", re.IGNORECASE)
# Structural by CPV only in two narrow cases: patented medicines named by active principle (3369*) and the
# universal postal service (6411*). Utilities (0931*, 0932*), maintenance (50*) and consumables are NOT
# structural by code: their single bids are often a choice, which is the point of the classification.
MEDICINE_CPV4 = "3369"
POSTAL_CPV4 = "6411"
EQUIPMENT_CONSUMABLE_CPV4 = ("3314", "3318")   # medical consumables and implants

LEVERS: dict[str, str] = {
    "oem_incumbent": "mantenimiento multimarca o cláusula de acceso a repuestos y formación",
    "peer_exclusive": "verificar la exclusividad técnica (informe art. 168) antes de repetir el pliego",
    "exclusivity_cue": "verificar la exclusividad técnica (informe art. 168) antes de repetir el pliego",
    "medicine_active_principle": "revisar alternativas terapéuticas con Farmacia antes del pliego",
    "postal_universal": "sin palanca en el pliego: servicio universal",
    "extension": "planificar antes del vencimiento para licitar en lugar de prorrogar",
    "framework": "la competencia se fijó en el acuerdo marco: actuar en su renovación",
    "compatibility_cue": "cláusula «o equivalente» y coste total de propiedad en la próxima compra de equipo",
    "brand_consumable": "cláusula «o equivalente» y coste total de propiedad en la próxima compra de equipo",
    "thin_market": "consulta preliminar de mercado (art. 115) e invitar a los que ganan en otros hospitales",
    "short_deadline": "ampliar el plazo de presentación de ofertas",
    "large_lot": "dividir en lotes para que entren más licitadores",
    "services": "consulta preliminar de mercado e invitar a los adjudicatarios de otros hospitales",
    "competitive_market": "consulta preliminar de mercado: el mismo objeto recibe varias ofertas en otros hospitales",
    "none": "consulta preliminar de mercado",
}
STRUCTURAL = ("oem_incumbent", "peer_exclusive", "exclusivity_cue", "medicine_active_principle", "postal_universal")
EARLIER = ("extension", "framework", "compatibility_cue", "brand_consumable")
ADDRESSABLE = ("competitive_market", "thin_market", "short_deadline", "large_lot", "services")


def signals_for(row: Mapping[str, Any], peer_rate: float | None, peer_n: int | None) -> list[str]:
    """Every signal that fires on one lot, in bucket order (structural, earlier decision, addressable)."""
    title = row.get("title") or ""
    cpv4 = (row.get("cpv4") or "")[:4]
    brand = bool(row.get("brand_in_title"))
    peer_ok = peer_rate is not None and peer_n is not None and peer_n >= PEER_MIN_N
    out: list[str] = []
    oem = brand and incumbent_is_manufacturer(title, row.get("winner"))
    if oem:
        out.append("oem_incumbent")
    if peer_ok and peer_rate >= PEER_EXCLUSIVE:
        out.append("peer_exclusive")
    if EXCLUSIVITY_RE.search(title):
        out.append("exclusivity_cue")
    if cpv4.startswith(MEDICINE_CPV4) and ACTIVE_PRINCIPLE_RE.search(title):
        out.append("medicine_active_principle")
    if cpv4.startswith(POSTAL_CPV4):
        out.append("postal_universal")
    if is_extension(title):
        out.append("extension")
    if is_framework_derived(title):
        out.append("framework")
    if COMPATIBILITY_RE.search(title):
        out.append("compatibility_cue")
    if brand and not oem and cpv4.startswith(EQUIPMENT_CONSUMABLE_CPV4):
        out.append("brand_consumable")
    if peer_ok and peer_rate < PEER_COMPETITIVE:
        out.append("competitive_market")
    if peer_ok and PEER_THIN <= peer_rate < PEER_EXCLUSIVE:
        out.append("thin_market")
    dd = row.get("deadline_days")
    if dd is not None and not pd.isna(dd) and dd < SHORT_DEADLINE_DAYS:
        out.append("short_deadline")
    lb = row.get("lot_budget")
    if lb is not None and not pd.isna(lb) and lb >= LARGE_LOT_EUR:
        out.append("large_lot")
    if cpv4[:2] in SERVICE_CPV2:
        out.append("services")
    return out


def bucket_for(row: Mapping[str, Any], peer_rate: float | None, peer_n: int | None) -> tuple[str, float, str, str]:
    """(bucket, confidence, signals, lever) for one lot. Structural beats earlier decision beats addressable:
    a prórroga of the manufacturer's maintenance is structural, the extension is how it shows."""
    sig = signals_for(row, peer_rate, peer_n)
    structural = [s for s in sig if s in STRUCTURAL]
    earlier = [s for s in sig if s in EARLIER]
    addressable = [s for s in sig if s in ADDRESSABLE]
    if structural:
        bucket, own = "structural", structural
    elif earlier:
        bucket, own = "earlier_decision", earlier
    else:
        bucket, own = "addressable", addressable
    if bucket == "addressable":
        confidence = 0.7 if "competitive_market" in own else 0.5
    else:
        confidence = 0.9 if len(own) >= 2 else 0.7
    lever = LEVERS[own[0]] if own else LEVERS["none"]
    return bucket, confidence, ",".join(sig), lever


LOTS_SQL = """
select l.buyer_key, l.buyer_nif, l.folder_id, l.lot_id, l.title_clean as title, l.winner, l.cpv4, l.band,
       l.lot_budget, l.award_net, l.award_d, l.deadline_days, l.brand_in_title,
       p.single_bid_rate as peer_rate, p.n as peer_n
from lots l left join peers p using (cpv4, band)
where l.is_open and l.n_bids = 1
"""
COLUMNS = ["buyer_key", "buyer_nif", "folder_id", "lot_id", "award_net", "award_d", "reason_bucket",
           "reason_confidence", "signals", "lever"]


def classify(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per single-bid open lot (same grain as `lots`), carrying the award so summaries need no
    re-join: lots without a lot id would multiply on (buyer_key, folder_id)."""
    df = con.execute(LOTS_SQL).df()
    rows = []
    for r in df.to_dict("records"):
        pr, pn = r.pop("peer_rate"), r.pop("peer_n")
        pr = None if pr is None or pd.isna(pr) else float(pr)
        pn = None if pn is None or pd.isna(pn) else int(pn)
        bucket, conf, sig, lever = bucket_for(r, pr, pn)
        rows.append({"buyer_key": r["buyer_key"], "buyer_nif": r["buyer_nif"], "folder_id": r["folder_id"],
                     "lot_id": r["lot_id"], "award_net": r["award_net"], "award_d": r["award_d"],
                     "reason_bucket": bucket, "reason_confidence": conf, "signals": sig, "lever": lever})
    return pd.DataFrame(rows, columns=COLUMNS)


def summary(df: pd.DataFrame, con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """Per buyer and bucket: lots, awarded euros and the buyer's value share in that bucket."""
    if df.empty:
        return pd.DataFrame(columns=["buyer_key", "reason_bucket", "n_lots", "award_net", "value_share"])
    g = df.groupby(["buyer_key", "reason_bucket"]).agg(n_lots=("folder_id", "size"), award_net=("award_net", "sum"))
    g = g.reset_index()
    g["value_share"] = g["award_net"] / g.groupby("buyer_key")["award_net"].transform("sum")
    return g.sort_values(["buyer_key", "reason_bucket"]).reset_index(drop=True)
