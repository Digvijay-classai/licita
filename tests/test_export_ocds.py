"""OCDS export: mappings, a release from a mini spine, person exclusion, counts, datasheet."""
import gzip
import json
import math
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from licita.export import ocds
from licita.export.datasheet import datasheet, known_issues

NIF = "Q0000001A"


@pytest.mark.parametrize("code,method", [("1", "open"), ("9", "open"), ("13", "open"), ("2", "selective"),
                                         ("3", "limited"), ("6", "direct"), ("7", "selective"), ("999", None), (None, None)])
def test_procurement_method(code, method):
    assert ocds.procurement_method(code) == method


def test_category_and_person_rules():
    assert ocds.main_category("1") == "goods" and ocds.main_category("3") == "works" and ocds.main_category("50") == "services"
    assert ocds.is_person("12345678Z", False) and ocds.is_person("X1234567L", None) and ocds.is_person("B12345678", True)
    assert not ocds.is_person("B12345678", False) and not ocds.is_person(None, None)
    assert ocds.ocid("k", "F1") == ocds.ocid("k", "F1") and ocds.ocid("k", "F1").startswith("ocds-licita-")


@pytest.fixture
def con():
    db = duckdb.connect()
    lots_json = json.dumps([{"lot_id": "1", "name": "Reactivos", "budget_net": 100.0, "cpvs": ["33696500"]},
                            {"lot_id": "2", "name": "Fungible", "budget_net": 50.0, "cpvs": ["33140000", "33141000"]}])
    folders = pd.DataFrame([
        dict(buyer_key=NIF, folder_id="F1", updated="2024-06-01T10:00:00+02:00", link="https://example.invalid/F1",
             title="Suministro de reactivos", status="RES", buyer="Hospital Santa Ficticia", buyer_nif=NIF, buyer_dir3="A0001",
             buyer_city="Villaejemplo", buyer_parents="Comunidad de Ejemplo", contract_type="1", cpvs="33696500,33140000",
             budget_net=150.0, budget_estimated=180.0, n_lots=2, lots_json=lots_json, procedure="1",
             submission_deadline="2024-02-01", notice_cn="2024-01-01", notice_award="2024-03-01", planned_start="2024-04-01",
             planned_end=None, duration=12.0, duration_unit="MON", source_feed="643", is_health=True),
        dict(buyer_key="DIR3:B9", folder_id="F2", updated="2023-03-01T10:00:00+01:00", link=None,
             title="Servicio de limpieza", status="ANUL", buyer="Centro de Ejemplo", buyer_nif=None, buyer_dir3="B9",
             buyer_city=None, buyer_parents=None, contract_type="2", cpvs="90910000", budget_net=float("nan"),
             budget_estimated=None, n_lots=0, lots_json=None, procedure="3", submission_deadline=None, notice_cn=None,
             notice_award="2023-02-01", planned_start=None, planned_end=None, duration=None, duration_unit=None,
             source_feed="1044", is_health=True),
        dict(buyer_key=NIF, folder_id="OLD", updated="2019-01-01T10:00:00+01:00", link=None, title="Antiguo", status="RES",
             buyer="Hospital Santa Ficticia", buyer_nif=NIF, buyer_dir3=None, buyer_city=None, buyer_parents=None,
             contract_type="2", cpvs="90910000", budget_net=10.0, budget_estimated=None, n_lots=0, lots_json=None,
             procedure="9", submission_deadline=None, notice_cn="2018-12-01", notice_award="2019-01-01", planned_start=None,
             planned_end=None, duration=None, duration_unit=None, source_feed="643", is_health=True),
    ])
    results = pd.DataFrame([
        dict(buyer_key=NIF, folder_id="F1", lot_id="1", result_code="9", award_date="2024-03-01", n_bids=2.0, n_sme_bids=1.0,
             winner="ACME, S.A.", winner_nif="A11111111", award_net=90.0, contract_id="C1", contract_date="2024-03-20",
             contract_start="2024-04-01", sme_awarded="true"),
        dict(buyer_key=NIF, folder_id="F1", lot_id="2", result_code="8", award_date="2024-03-01", n_bids=1.0, n_sme_bids=None,
             winner="Juan Ejemplo", winner_nif="12345678Z", award_net=45.0, contract_id=None, contract_date=None,
             contract_start=None, sme_awarded=None),
        dict(buyer_key=NIF, folder_id="F1", lot_id="3", result_code="3", award_date=None, n_bids=0.0, n_sme_bids=None,
             winner=None, winner_nif=None, award_net=None, contract_id=None, contract_date=None, contract_start=None,
             sme_awarded=None),
        dict(buyer_key="DIR3:B9", folder_id="F2", lot_id=None, result_code="9", award_date=None, n_bids=3.0, n_sme_bids=None,
             winner="Beta SL", winner_nif="B22222222", award_net=1000.0, contract_id=None, contract_date="2023-02-15",
             contract_start=None, sme_awarded="false"),
        dict(buyer_key=NIF, folder_id="OLD", lot_id=None, result_code="9", award_date="2019-01-01", n_bids=1.0, n_sme_bids=None,
             winner="Beta SL", winner_nif="B22222222", award_net=10.0, contract_id=None, contract_date=None,
             contract_start=None, sme_awarded=None),
    ])
    suppliers = pd.DataFrame([dict(winner_nif="A11111111", canonical_name="Acme SA", is_person=False),
                              dict(winner_nif="B22222222", canonical_name="Beta SL", is_person=False),
                              dict(winner_nif="12345678Z", canonical_name="Juan Ejemplo", is_person=True)])
    lots = pd.DataFrame([dict(buyer_key=NIF, award_d=date(2024, 3, 1)), dict(buyer_key="DIR3:B9", award_d=date(2023, 2, 15))])
    for name, df in (("folders", folders), ("results", results), ("suppliers", suppliers), ("lots", lots)):
        db.register(f"{name}_df", df)
        db.execute(f"create table {name} as select * from {name}_df")
    db.execute("create table meta as select 'r1' as run_id, date '2024-07-01' as as_of, date '2024-06-30' as data_through, "
               "2 as schema_version")
    return db


def _no_nulls(obj):
    if isinstance(obj, dict):
        assert all(v is not None and v != "" for v in obj.values()), obj
        for v in obj.values():
            _no_nulls(v)
    elif isinstance(obj, list):
        assert obj, "empty list left in release"
        for v in obj:
            _no_nulls(v)
    elif isinstance(obj, float):
        assert not math.isnan(obj)


def test_release_shape_and_mapping(con):
    out = dict((r["tender"]["id"], (r, x)) for r, x in ocds.releases(con, since=None, buyer_keys=None))
    r, excluded = out["F1"]
    _no_nulls(r)
    json.dumps(r, allow_nan=False)
    assert r["tag"] == ["compiled"] and r["initiationType"] == "tender" and r["date"] == "2024-06-01T10:00:00+02:00"
    assert r["buyer"]["id"] == f"ES-NIF-{NIF}" and r["parties"][0]["identifier"] == {"scheme": "ES-NIF", "id": NIF}
    t = r["tender"]
    assert t["procurementMethod"] == "open" and t["procurementMethodDetails"].startswith("PLACSP 1")
    assert t["mainProcurementCategory"] == "goods" and t["status"] == "complete"
    assert t["value"] == {"amount": 150.0, "currency": "EUR"} and t["tenderPeriod"]["endDate"] == "2024-02-01T00:00:00Z"
    assert t["contractPeriod"]["durationInDays"] == 365 and t["contractPeriod"]["startDate"] == "2024-04-01T00:00:00Z"
    assert [i["classification"]["id"] for i in t["items"]] == ["33696500", "33140000"]
    assert t["items"][1]["additionalClassifications"] == [{"scheme": "CPV", "id": "33141000"}]
    assert "numberOfTenderers" not in t                                  # multi-lot: bids are per lot
    # awards: lot 1 active with the supplier's canonical name; lot 2 (natural person) excluded; lot 3 deserted
    assert [(a["status"], a.get("relatedLots")) for a in r["awards"]] == [("active", ["1"]), ("unsuccessful", ["3"])]
    assert r["awards"][0]["suppliers"] == [{"id": "ES-NIF-A11111111", "name": "Acme SA"}]
    assert r["awards"][0]["date"] == "2024-03-01T00:00:00Z" and r["awards"][0]["value"]["amount"] == 90.0
    assert excluded == 1
    supplier = next(p for p in r["parties"] if p["id"] == "ES-NIF-A11111111")
    assert supplier["roles"] == ["supplier"] and supplier["details"] == {"scale": "sme"}
    assert r["contracts"] == [{"id": "C1", "awardID": r["awards"][0]["id"], "status": "active",
                               "dateSigned": "2024-03-20T00:00:00Z", "period": {"startDate": "2024-04-01T00:00:00Z"},
                               "value": {"amount": 90.0, "currency": "EUR"}}]
    assert not any(p["name"] == "Juan Ejemplo" for p in r["parties"])


def test_dir3_buyer_nan_budget_and_single_lot_bids(con):
    r = next(r for r, _ in ocds.releases(con) if r["tender"]["id"] == "F2")
    _no_nulls(r)
    assert r["buyer"]["id"] == "ES-DIR3-B9" and r["tender"]["status"] == "cancelled"
    assert r["tender"]["procurementMethod"] == "limited" and "value" not in r["tender"]
    assert r["tender"]["numberOfTenderers"] == 3
    assert r["awards"][0]["date"] == "2023-02-15T00:00:00Z"            # contract date stands in for the award date
    assert r["tender"]["items"][0]["classification"]["id"] == "90910000"   # CPV from the folder when no lot list


def test_since_and_buyer_filters(con):
    ids = sorted(r["tender"]["id"] for r, _ in ocds.releases(con, since=date(2022, 1, 1)))
    assert ids == ["F1", "F2"]
    ids = sorted(r["tender"]["id"] for r, _ in ocds.releases(con, since=None, buyer_keys=[NIF]))
    assert ids == ["F1", "OLD"]


def test_write_jsonl_counts_and_gzip(con, tmp_path):
    out = tmp_path / "x.jsonl.gz"
    counts = ocds.write_jsonl(out, ocds.releases(con))
    assert counts == {"releases": 3, "awards": 3, "awards_unsuccessful": 1, "contracts": 2, "excluded_persons": 1, "buyers": 2}
    with gzip.open(out, "rt", encoding="utf-8") as f:
        lines = [json.loads(ln) for ln in f]
    assert len(lines) == 3 and all("ocid" in ln for ln in lines)
    assert len({ln["ocid"] for ln in lines}) == 3


def test_datasheet_sections_and_counts(con, tmp_path):
    dq = tmp_path / "dq_report.md"
    dq.write_text("# DQ\n\n| check | level | status | value | rule |\n|---|---|---|---|---|\n| folders_unique | hard | pass | 3 | x |\n")
    text = datasheet(con, {"releases": 3, "awards": 3, "awards_unsuccessful": 1, "contracts": 2, "excluded_persons": 1,
                           "buyers": 2, "since": "2022-01-01", "ocid_prefix": "ocds-licita-"}, dq)
    for h in ("## Motivation", "## Composition", "## Collection process", "## Preprocessing and cleaning", "## Uses",
              "## Distribution", "## Maintenance"):
        assert h in text
    assert "**3 releases**" in text and "1 award rows were dropped" in text and "| folders_unique | hard | pass |" in text
    assert "data through **2024-06-30**" in text and "not registered" in text
    assert "to confirm before release" in text


def test_known_issues_reads_the_data_notes(tmp_path):
    md = tmp_path / "DATA.md"
    md.write_text("# Data\n\n## Known data-quality issues and their handling\n\n| Issue | Handling |\n|---|---|\n| a | b |\n\n## What is not in git\n\nx\n")
    assert known_issues(md) == "| Issue | Handling |\n|---|---|\n| a | b |"
    assert known_issues(Path("/nonexistent/DATA.md")).startswith("See")
    assert "| Issue | Handling |" in known_issues()      # the real file has the table
