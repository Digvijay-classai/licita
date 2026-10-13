"""OCDS 1.1 export of the spine: one compiled release per tender, streamed to gzip JSONL.

  python -m licita.export.ocds --since 2022-01-01 --all
  python -m licita.export.ocds --since 2022-01-01 --buyers HOSP_A,HOSP_B --out out/ocds/x.jsonl.gz

What a release holds: buyer and supplier parties, the tender (procedure, category, budget, deadline, lots as
items), one award per awarded lot (status "active") or deserted lot (status "unsuccessful"), and contracts
where PLACSP published a contract id or date. Nothing else is invented: fields PLACSP did not publish are
absent, never null.

OCID prefix: "ocds-licita-" is a PLACEHOLDER, not a prefix registered with the Open Contracting
Partnership. Before a public release, obtain a prefix (https://www.open-contracting.org/resources/registration/)
and set OCID_PREFIX; identifiers stay stable because the hash part does not change.

Personal data: awards to natural persons (DNI/NIE-shaped winner ids, or `suppliers.is_person`) are dropped
from the export and counted in the sidecar and the datasheet.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from licita.config import OUT_DIR, SPINES_DIR

OCID_PREFIX = "ocds-licita-"          # placeholder; see module docstring
PERSON_RE = re.compile(r"^[0-9]{8}[A-Z]$|^[XYZ][0-9]{7}[A-Z]$")
AWARD_CODES = ("2", "8", "9", "11")
DESERTED_CODES = ("3",)

# PLACSP ProcedureCode -> OCDS procurementMethod (open | selective | limited | direct).
# 1 Abierto, 2 Restringido, 3 Negociado sin publicidad, 4 Negociado con publicidad, 5 Diálogo competitivo,
# 6 Contrato menor, 7 Derivado de acuerdo marco, 8 Concurso de proyectos, 9 Abierto simplificado,
# 10 Asociación para la innovación, 11 Diálogo competitivo (variant), 12 Licitación con negociación,
# 13 Abierto simplificado abreviado, 100/999 otros. The raw code always goes to procurementMethodDetails.
PROCEDURE_METHOD = {
    "1": "open", "9": "open", "13": "open", "8": "open",
    "2": "selective", "4": "selective", "5": "selective", "10": "selective", "11": "selective", "12": "selective",
    "7": "selective",      # framework call-off: competition happened at framework level or in a mini-competition
    "3": "limited",        # negotiated without publicity
    "6": "direct",         # minor contract
}
PROCEDURE_LABEL = {
    "1": "Abierto", "2": "Restringido", "3": "Negociado sin publicidad", "4": "Negociado con publicidad",
    "5": "Diálogo competitivo", "6": "Contrato menor", "7": "Basado en acuerdo marco", "8": "Concurso de proyectos",
    "9": "Abierto simplificado", "10": "Asociación para la innovación", "11": "Diálogo competitivo",
    "12": "Licitación con negociación", "13": "Abierto simplificado abreviado", "100": "Otros", "999": "Otros",
}
CATEGORY = {"1": "goods", "2": "services", "3": "works"}
TENDER_STATUS = {"PRE": "planned", "PUB": "active", "EV": "active", "ADJ": "complete", "RES": "complete",
                 "ANUL": "cancelled"}


def procurement_method(code: str | None) -> str | None:
    return PROCEDURE_METHOD.get(str(code)) if code is not None else None


def main_category(contract_type: str | None) -> str:
    return CATEGORY.get(str(contract_type), "services")


def is_person(winner_nif: str | None, flag: bool | None) -> bool:
    return bool(flag) or bool(winner_nif and PERSON_RE.match(winner_nif))


def ocid(buyer_key: str, folder_id: str) -> str:
    return OCID_PREFIX + hashlib.sha1(f"{buyer_key}|{folder_id}".encode()).hexdigest()[:16]


def _dt(v) -> str | None:
    """ISO 8601 date-time. Plain dates get midnight UTC; PLACSP timestamps keep their offset."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return f"{v.isoformat()}T00:00:00Z"
    s = str(v).strip()
    if not s:
        return None
    if len(s) == 10:
        return f"{s}T00:00:00Z"
    return s


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _clean(obj):
    """Drop None, NaN, empty strings, empty lists and empty dicts recursively."""
    if isinstance(obj, dict):
        out = {k: _clean(v) for k, v in obj.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        out = [_clean(v) for v in obj]
        return [v for v in out if v not in (None, "", [], {})]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


def buyer_party(buyer_key: str, buyer_nif: str | None, buyer_dir3: str | None, name: str | None,
                city: str | None, parents: str | None) -> dict:
    if buyer_nif:
        pid = {"scheme": "ES-NIF", "id": buyer_nif}
    elif buyer_dir3:
        pid = {"scheme": "ES-DIR3", "id": buyer_dir3}
    else:
        pid = {"scheme": "name", "id": buyer_key}
    return {"id": f"{pid['scheme']}-{pid['id']}", "name": name, "identifier": pid, "roles": ["buyer"],
            "address": {"locality": city}, "additionalIdentifiers": None,
            "details": {"parentOrganisations": parents} if parents else None}


def _items(lots_json: str | None, cpvs: str | None) -> list[dict]:
    lots = []
    if lots_json:
        try:
            lots = json.loads(lots_json)
        except json.JSONDecodeError:
            lots = []
    items = []
    for lot in lots:
        codes = lot.get("cpvs") or []
        items.append({
            "id": str(lot.get("lot_id") or len(items) + 1),
            "description": lot.get("name"),
            "classification": {"scheme": "CPV", "id": codes[0]} if codes else None,
            "additionalClassifications": [{"scheme": "CPV", "id": c} for c in codes[1:]],
            "relatedLot": str(lot.get("lot_id")) if lot.get("lot_id") else None,
            "unit": {"value": {"amount": _num(lot.get("budget_net")), "currency": "EUR"}}
            if _num(lot.get("budget_net")) is not None else None,
        })
    if not items and cpvs:
        codes = [c for c in cpvs.split(",") if c]
        items.append({"id": "1", "classification": {"scheme": "CPV", "id": codes[0]} if codes else None,
                      "additionalClassifications": [{"scheme": "CPV", "id": c} for c in codes[1:]]})
    return items


FOLDER_SQL = """
with f as (
  select f.buyer_key, f.folder_id, f.updated, f.link, f.title, f.status, f.buyer, f.buyer_nif, f.buyer_dir3,
         f.buyer_city, f.buyer_parents, f.contract_type, f.cpvs, f.budget_net, f.budget_estimated, f.n_lots,
         f.lots_json, f.procedure, f.submission_deadline, f.notice_cn, f.notice_award, f.planned_start,
         f.planned_end, f.duration, f.duration_unit, f.source_feed
  from folders f
  where {where}
),
r as (
  select r.buyer_key, r.folder_id,
         list(struct_pack(lot_id := r.lot_id, result_code := r.result_code,
                          award_d := coalesce(try_cast(r.award_date as date), try_cast(left(r.contract_date, 10) as date)),
                          n_bids := r.n_bids, n_sme_bids := r.n_sme_bids, winner := r.winner, winner_nif := r.winner_nif,
                          award_net := r.award_net, contract_id := r.contract_id, contract_date := r.contract_date,
                          contract_start := r.contract_start, sme_awarded := r.sme_awarded,
                          canonical := s.canonical_name, person := coalesce(s.is_person, false))
              order by r.lot_id, r.winner_nif) as results
  from results r
  left join suppliers s using (winner_nif)
  where r.result_code in ('2', '8', '9', '11', '3')
  group by 1, 2
)
select f.*, r.results from f left join r using (buyer_key, folder_id)
order by f.buyer_key, f.folder_id
"""


def release_from_row(row: dict) -> tuple[dict, int]:
    """(release, excluded natural-person awards) from one joined folder row."""
    buyer_key, folder_id = row["buyer_key"], row["folder_id"]
    buyer = buyer_party(buyer_key, row.get("buyer_nif"), row.get("buyer_dir3"), row.get("buyer"),
                        row.get("buyer_city"), row.get("buyer_parents"))
    parties = {buyer["id"]: buyer}
    awards, contracts, excluded = [], [], 0
    n_bids_single = None
    for i, res in enumerate(row.get("results") or [], 1):
        code = str(res.get("result_code"))
        lot_id = res.get("lot_id")
        if code in AWARD_CODES:
            if is_person(res.get("winner_nif"), res.get("person")):
                excluded += 1
                continue
            nif = res.get("winner_nif")
            name = res.get("canonical") or res.get("winner")
            sid = f"ES-NIF-{nif}" if nif else f"name-{name}"
            if sid not in parties:
                parties[sid] = {"id": sid, "name": name, "identifier": {"scheme": "ES-NIF", "id": nif} if nif else None,
                                "roles": ["supplier"],
                                "details": {"scale": "sme"} if str(res.get("sme_awarded")).lower() in ("true", "1") else None}
            elif "supplier" not in parties[sid]["roles"]:
                parties[sid]["roles"].append("supplier")
            aid = f"{folder_id}-{lot_id or 'all'}-{i}"
            awards.append({"id": aid, "status": "active", "date": _dt(res.get("award_d")),
                           "value": {"amount": _num(res.get("award_net")), "currency": "EUR"}
                           if _num(res.get("award_net")) is not None else None,
                           "suppliers": [{"id": sid, "name": name}],
                           "relatedLots": [str(lot_id)] if lot_id else None})
            if res.get("contract_id") or res.get("contract_date"):
                contracts.append({"id": str(res.get("contract_id") or aid), "awardID": aid, "status": "active",
                                  "dateSigned": _dt(res.get("contract_date")),
                                  "period": {"startDate": _dt(res.get("contract_start"))} if res.get("contract_start") else None,
                                  "value": {"amount": _num(res.get("award_net")), "currency": "EUR"}
                                  if _num(res.get("award_net")) is not None else None})
        elif code in DESERTED_CODES:
            awards.append({"id": f"{folder_id}-{lot_id or 'all'}-{i}", "status": "unsuccessful",
                           "date": _dt(res.get("award_d")), "relatedLots": [str(lot_id)] if lot_id else None,
                           "description": "Desierto: no se presentó oferta admisible"})
        if (row.get("n_lots") or 0) <= 1 and _num(res.get("n_bids")) is not None:
            n_bids_single = int(_num(res.get("n_bids")))
    proc = row.get("procedure")
    tender = {
        "id": folder_id, "title": row.get("title"), "status": TENDER_STATUS.get(str(row.get("status"))),
        "procurementMethod": procurement_method(proc),
        "procurementMethodDetails": f"PLACSP {proc}: {PROCEDURE_LABEL.get(str(proc), 'desconocido')}" if proc else None,
        "mainProcurementCategory": main_category(row.get("contract_type")),
        "value": {"amount": _num(row.get("budget_net")), "currency": "EUR"} if _num(row.get("budget_net")) is not None else None,
        "tenderPeriod": {"endDate": _dt(row.get("submission_deadline"))} if row.get("submission_deadline") else None,
        "contractPeriod": {"startDate": _dt(row.get("planned_start")), "endDate": _dt(row.get("planned_end")),
                           "durationInDays": _duration_days(row.get("duration"), row.get("duration_unit"))}
        if (row.get("planned_start") or row.get("planned_end") or row.get("duration")) else None,
        "items": _items(row.get("lots_json"), row.get("cpvs")),
        "numberOfTenderers": n_bids_single,
        "documents": [{"id": "placsp", "documentType": "tenderNotice", "url": row.get("link")}] if row.get("link") else None,
    }
    rel = {
        "ocid": ocid(buyer_key, folder_id), "id": f"{ocid(buyer_key, folder_id)}-{str(row.get('updated'))[:10]}",
        "date": _dt(row.get("updated")), "tag": ["compiled"], "initiationType": "tender", "language": "es",
        "buyer": {"id": buyer["id"], "name": buyer.get("name")},
        "parties": list(parties.values()), "tender": tender, "awards": awards, "contracts": contracts,
        "source": {"feed": f"PLACSP {row.get('source_feed')}"} if row.get("source_feed") else None,
    }
    return _clean(rel), excluded


def _duration_days(duration, unit) -> int | None:
    d = _num(duration)
    if d is None:
        return None
    factor = {"DAY": 1, "WEE": 7, "MON": 30.4, "ANN": 365}.get(str(unit))
    return int(round(d * factor)) if factor else None


def releases(con: duckdb.DuckDBPyConnection, since: date | None = None, buyer_keys: list[str] | None = None,
             health_only: bool = True, batch: int = 500) -> Iterator[tuple[dict, int]]:
    """Yield (release, excluded_persons) per folder, streaming from the spine."""
    where, params = ["true"], []
    if health_only:
        where.append("f.is_health")
    if since is not None:
        where.append("coalesce(try_cast(f.notice_award as date), try_cast(f.notice_cn as date), "
                     "try_cast(left(f.updated, 10) as date)) >= ?")
        params.append(since)
    if buyer_keys:
        where.append(f"f.buyer_key in ({','.join('?' * len(buyer_keys))})")
        params.extend(buyer_keys)
    cur = con.execute(FOLDER_SQL.format(where=" and ".join(where)), params)
    cols = [d[0] for d in cur.description]
    while True:
        rows = cur.fetchmany(batch)
        if not rows:
            break
        for r in rows:
            yield release_from_row(dict(zip(cols, r, strict=True)))


def write_jsonl(path: Path, releases_iter: Iterator[tuple[dict, int]]) -> dict:
    """Write releases as JSONL (gzip when the name ends in .gz). Returns counts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = {"releases": 0, "awards": 0, "awards_unsuccessful": 0, "contracts": 0, "excluded_persons": 0}
    buyers: set[str] = set()
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wt", encoding="utf-8") as f:
        for rel, excluded in releases_iter:
            f.write(json.dumps(rel, ensure_ascii=False, allow_nan=False) + "\n")
            counts["releases"] += 1
            counts["awards"] += sum(1 for a in rel.get("awards", []) if a.get("status") == "active")
            counts["awards_unsuccessful"] += sum(1 for a in rel.get("awards", []) if a.get("status") == "unsuccessful")
            counts["contracts"] += len(rel.get("contracts", []))
            counts["excluded_persons"] += excluded
            buyers.add(rel["buyer"]["id"])
    counts["buyers"] = len(buyers)
    return counts


def main(argv: list[str] | None = None) -> int:
    from licita.export.datasheet import datasheet
    from licita.profile import load_profile
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spine", type=Path, default=SPINES_DIR / "spine.duckdb")
    ap.add_argument("--since", type=date.fromisoformat, default=None, help="first notice/award date to include")
    ap.add_argument("--buyers", default="", help="comma-separated buyer keys from the profile")
    ap.add_argument("--all", action="store_true", help="every health buyer in the spine")
    ap.add_argument("--out", type=Path, default=None, help="output .jsonl or .jsonl.gz (default out/ocds/health_<data_through>.jsonl.gz)")
    args = ap.parse_args(argv)
    if not args.all and not args.buyers:
        ap.error("choose --all or --buyers KEY[,KEY]")
    con = duckdb.connect(str(args.spine), read_only=True)
    run_id, through, schema = con.execute("select run_id, data_through, schema_version from meta").fetchone()
    keys = None
    if args.buyers:
        prof = load_profile()
        keys = [prof.buyer(k).nif for k in args.buyers.split(",") if k]
    out = args.out or (OUT_DIR / "ocds" / f"health_{through}.jsonl.gz")
    counts = write_jsonl(out, releases(con, args.since, keys))
    meta = {"spine_run_id": run_id, "data_through": str(through), "spine_schema": schema, "ocds_version": "1.1",
            "ocid_prefix": OCID_PREFIX, "ocid_prefix_registered": False, "since": str(args.since) if args.since else None,
            "buyers_scope": args.buyers or "all-health", "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "counts": counts}
    base = str(out).removesuffix(".gz").removesuffix(".jsonl")
    Path(base + ".meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    from licita.config import RUNS_DIR
    dq = RUNS_DIR / run_id / "dq_report.md"
    Path(base + ".DATASHEET.md").write_text(datasheet(con, {**counts, **{k: v for k, v in meta.items() if k != "counts"}},
                                                       dq if dq.exists() else None))
    print(json.dumps(meta, indent=1, ensure_ascii=False))
    print(f"-> {out}\n-> {base}.meta.json\n-> {base}.DATASHEET.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
