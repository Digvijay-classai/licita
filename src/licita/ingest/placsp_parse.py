"""Stream PLACSP CODICE atom archives into Parquet (one pass, constant memory).

Outputs per archive:
  <prefix>_folders.parquet   one row per entry snapshot (all snapshots kept, so the earliest
                             version of a pliego is recoverable after it is replaced)
  <prefix>_results.parquet   one row per TenderResult (lot-level award)

Kept rows: health-sector buyers (name regex), health CPVs (33*/85*), and every buyer whose parent
chain matches the profile's `region.buyer_chain_re`. The last group catches central regional
purchasing that a hospital contract may move into (e.g. laundry or postal services under a
regional framework). Changing that regex needs a re-parse of the archives.
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import IO

import pandas as pd
from lxml import etree

from licita.profile import load_profile

PARSER_VERSION = 1   # bump when a column is added or a kept-row rule changes; `licita sync` then re-parses

# Fixed column lists so an archive with no kept rows still produces files with the full schema
# (read_parquet(union_by_name) would otherwise see a zero-column file).
FOLDER_COLUMNS = [
    "entry_id", "updated", "link", "title", "folder_id", "status", "buyer", "buyer_nif", "buyer_dir3", "buyer_city",
    "buyer_parents", "buyer_type", "is_health", "contract_type", "contract_subtype", "cpvs", "budget_estimated",
    "budget_total", "budget_net", "nuts", "planned_start", "planned_end", "duration", "duration_unit",
    "extension_desc", "extension_period", "n_lots", "lots_json", "procedure", "urgency", "contracting_system",
    "over_threshold", "submission_deadline", "funding", "attachments_json", "notice_cn", "notice_pin",
    "notice_award", "notice_formal", "notices", "source_feed", "source",
]
RESULT_COLUMNS = [
    "entry_id", "updated", "folder_id", "buyer_nif", "source_feed", "lot_id", "result_code", "result_desc",
    "award_date", "n_bids", "n_sme_bids", "lower_bid", "higher_bid", "winner", "winner_nif", "sme_awarded",
    "award_net", "contract_id", "contract_date", "contract_start",
]

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
    "cbcx": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2",
}
ENTRY = f"{{{NS['a']}}}entry"

# Generic health nouns in Spain's co-official languages; a regional service's own names and acronyms come
# from the profile (`region.health_terms`), so no region's naming convention lives in this file.
HEALTH_RE = re.compile(r"hospital|salud|salut|sa[uú]de|osasun|sanitari|ingesa|cl[ií]nic", re.I)


@cache
def _region_res(chain: str, extra: tuple[str, ...]) -> tuple[re.Pattern | None, re.Pattern | None]:
    return (re.compile(chain, re.I) if chain else None,
            re.compile("|".join(map(re.escape, extra)), re.I) if extra else None)


def region_res() -> tuple[re.Pattern | None, re.Pattern | None]:
    """(parent-chain regex, extra health-term regex) from the active profile; either may be None."""
    r = load_profile().region
    return _region_res(r.buyer_chain_re, r.health_terms)


def is_health_buyer(buyer: str | None, cpvs: list[str]) -> bool:
    _, extra = region_res()
    return (bool(HEALTH_RE.search(buyer or "")) or bool(extra and extra.search(buyer or ""))
            or any(c.startswith(("33", "85")) for c in cpvs))

ATTACHMENT_PATHS = {
    "legal": "cac:LegalDocumentReference",          # PCAP (administrative clauses)
    "technical": "cac:TechnicalDocumentReference",  # PPT (technical specifications)
    "additional": "cac:AdditionalDocumentReference",
    "general": "ext:GeneralDocument/ext:GeneralDocumentDocumentReference",
}


def _t(el, path: str) -> str | None:
    if el is None:
        return None
    r = el.find(path, NS)
    return r.text.strip() if r is not None and r.text else None


def _ts(el, path: str) -> list[str]:
    return [] if el is None else [r.text.strip() for r in el.findall(path, NS) if r.text]


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _attachments(f) -> list[dict]:
    out = []
    for kind, path in ATTACHMENT_PATHS.items():
        for ref in f.findall(path, NS):
            uri = _t(ref, "cac:Attachment/cac:ExternalReference/cbc:URI")
            if uri:
                out.append({
                    "kind": kind,
                    "id": _t(ref, "cbc:ID"),
                    "file": _t(ref, "cac:Attachment/cac:ExternalReference/cbc:FileName"),
                    "type": _t(ref, "cbc:DocumentTypeCode"),
                    "uri": uri,
                })
    return out


def parse_entry(e, source: str, feed: str) -> tuple[dict | None, list[dict]]:
    f = e.find("ext:ContractFolderStatus", NS)
    if f is None:
        return None, []
    party = f.find("ext:LocatedContractingParty", NS)
    buyer = _t(party, "cac:Party/cac:PartyName/cbc:Name")
    parents: list[str] = []
    p = party.find("ext:ParentLocatedParty", NS) if party is not None else None
    while p is not None:
        parents.append(_t(p, "cac:PartyName/cbc:Name") or "")
        p = p.find("ext:ParentLocatedParty", NS)
    parent_str = " > ".join(parents)
    proj = f.find("cac:ProcurementProject", NS)
    cpvs = _ts(proj, "cac:RequiredCommodityClassification/cbc:ItemClassificationCode")

    chain_re, _ = region_res()
    keep = is_health_buyer(buyer, cpvs) or bool(chain_re and chain_re.search(parent_str))
    if not keep:
        return None, []

    folder_id = _t(f, "cbc:ContractFolderID")
    buyer_nif = _t(party, "cac:Party/cac:PartyIdentification/cbc:ID[@schemeName='NIF']")
    notices: dict[str, str] = {}
    for vni in f.findall("ext:ValidNoticeInfo", NS):
        code = _t(vni, "cbcx:NoticeTypeCode")
        for d in vni.findall("ext:AdditionalPublicationStatus/ext:AdditionalPublicationDocumentReference/cbc:IssueDate", NS):
            if code and d.text:
                notices[code] = min(notices.get(code, d.text[:10]), d.text[:10])
    lots = f.findall("cac:ProcurementProjectLot", NS)
    dur = proj.find("cac:PlannedPeriod/cbc:DurationMeasure", NS) if proj is not None else None
    link = e.find("a:link", NS)

    row = {
        "entry_id": _t(e, "a:id"),
        "updated": _t(e, "a:updated"),
        "link": link.get("href") if link is not None else None,
        "title": _t(e, "a:title"),
        "folder_id": folder_id,
        "status": _t(f, "cbcx:ContractFolderStatusCode"),
        "buyer": buyer,
        "buyer_nif": buyer_nif,
        "buyer_dir3": _t(party, "cac:Party/cac:PartyIdentification/cbc:ID[@schemeName='DIR3']"),
        "buyer_city": _t(party, "cac:Party/cac:PostalAddress/cbc:CityName"),
        "buyer_parents": parent_str,
        "buyer_type": _t(party, "cbc:ContractingPartyTypeCode"),
        "is_health": is_health_buyer(buyer, cpvs),
        "contract_type": _t(proj, "cbc:TypeCode"),
        "contract_subtype": _t(proj, "cbc:SubTypeCode"),
        "cpvs": ",".join(cpvs),
        "budget_estimated": _num(_t(proj, "cac:BudgetAmount/cbc:EstimatedOverallContractAmount")),
        "budget_total": _num(_t(proj, "cac:BudgetAmount/cbc:TotalAmount")),
        "budget_net": _num(_t(proj, "cac:BudgetAmount/cbc:TaxExclusiveAmount")),
        "nuts": _t(proj, "cac:RealizedLocation/cbc:CountrySubentityCode"),
        "planned_start": _t(proj, "cac:PlannedPeriod/cbc:StartDate"),
        "planned_end": _t(proj, "cac:PlannedPeriod/cbc:EndDate"),
        "duration": _num(dur.text if dur is not None else None),
        "duration_unit": dur.get("unitCode") if dur is not None else None,
        "extension_desc": _t(proj, "cac:ContractExtension/cbc:OptionsDescription"),
        "extension_period": _t(proj, "cac:ContractExtension/cac:OptionValidityPeriod/cbc:Description"),
        "n_lots": len(lots),
        "lots_json": json.dumps([
            {
                "lot_id": _t(lot, "cbc:ID"),
                "name": _t(lot, "cac:ProcurementProject/cbc:Name"),
                "budget_net": _num(_t(lot, "cac:ProcurementProject/cac:BudgetAmount/cbc:TaxExclusiveAmount")),
                "cpvs": _ts(lot, "cac:ProcurementProject/cac:RequiredCommodityClassification/cbc:ItemClassificationCode"),
            }
            for lot in lots
        ], ensure_ascii=False) if lots else None,
        "procedure": _t(f, "cac:TenderingProcess/cbc:ProcedureCode"),
        "urgency": _t(f, "cac:TenderingProcess/cbc:UrgencyCode"),
        "contracting_system": _t(f, "cac:TenderingProcess/cbc:ContractingSystemCode"),
        "over_threshold": _t(f, "cac:TenderingProcess/cbc:OverThresholdIndicator"),
        "submission_deadline": _t(f, "cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndDate"),
        "funding": _t(f, "cac:TenderingTerms/cbc:FundingProgramCode"),
        "attachments_json": json.dumps(_attachments(f), ensure_ascii=False),
        "notice_cn": notices.get("DOC_CN"),
        "notice_pin": notices.get("DOC_PIN"),
        "notice_award": notices.get("DOC_CAN_ADJ"),
        "notice_formal": notices.get("DOC_FORM"),
        "notices": ",".join(sorted(notices)),
        "source_feed": feed,
        "source": source,
    }

    results = []
    for r in f.findall("cac:TenderResult", NS):
        results.append({
            "entry_id": row["entry_id"],
            "updated": row["updated"],
            "folder_id": folder_id,
            "buyer_nif": buyer_nif,
            "source_feed": feed,
            "lot_id": _t(r, "cac:AwardedTenderedProject/cbc:ProcurementProjectLotID"),
            "result_code": _t(r, "cbc:ResultCode"),
            "result_desc": _t(r, "cbc:Description"),
            "award_date": _t(r, "cbc:AwardDate"),
            "n_bids": _num(_t(r, "cbc:ReceivedTenderQuantity")),
            "n_sme_bids": _num(_t(r, "cbc:SMEsReceivedTenderQuantity")),
            "lower_bid": _num(_t(r, "cbc:LowerTenderAmount")),
            "higher_bid": _num(_t(r, "cbc:HigherTenderAmount")),
            "winner": _t(r, "cac:WinningParty/cac:PartyName/cbc:Name"),
            "winner_nif": _t(r, "cac:WinningParty/cac:PartyIdentification/cbc:ID"),
            "sme_awarded": _t(r, "cbc:SMEAwardedIndicator"),
            "award_net": _num(_t(r, "cac:AwardedTenderedProject/cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount")),
            "contract_id": _t(r, "cac:Contract/cbc:ID"),
            "contract_date": _t(r, "cac:Contract/cbc:IssueDate"),
            "contract_start": _t(r, "cac:StartDate") or _t(r, "cbc:StartDate"),
        })
    return row, results


def _streams(src: Path) -> Iterator[tuple[str, IO[bytes]]]:
    if src.is_dir():
        for f in sorted(src.glob("*.atom")):
            yield f.name, open(f, "rb")  # noqa: SIM115 (the caller closes each stream)
    else:
        z = zipfile.ZipFile(src)
        for n in sorted(z.namelist()):
            if n.endswith(".atom"):
                yield n, z.open(n)


def parse_archive(src: Path, out_prefix: Path, feed: str) -> dict:
    """Parse one archive; write parquet; return counts for the build report."""
    folders, results, n_seen = [], [], 0
    for name, stream in _streams(src):
        with stream:
            for _, e in etree.iterparse(stream, events=("end",), tag=ENTRY, huge_tree=True, recover=True):
                n_seen += 1
                row, res = parse_entry(e, name, feed)
                if row:
                    folders.append(row)
                    results.extend(res)
                e.clear()
                while e.getprevious() is not None:
                    del e.getparent()[0]
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    # Write both files to temporary names and rename at the end: a reader (or a build started by the
    # scheduler) never sees a half-written file, and a failed parse leaves the previous copy in place.
    pairs = [(pd.DataFrame(folders, columns=FOLDER_COLUMNS), Path(f"{out_prefix}_folders.parquet")),
             (pd.DataFrame(results, columns=RESULT_COLUMNS), Path(f"{out_prefix}_results.parquet"))]
    tmps = []
    for df, dest in pairs:
        tmp = dest.with_name(dest.name + ".tmp")
        df.to_parquet(tmp, index=False)
        tmps.append((tmp, dest))
    for tmp, dest in tmps:
        os.replace(tmp, dest)
    return {"entries": n_seen, "kept": len(folders), "results": len(results)}
