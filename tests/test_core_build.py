"""The core join (snapshots -> folders, results) on hand-made archives: awards paged across snapshots are
kept, stale non-award rows are not, overlapping archives do not duplicate, and buyers resolve to one key."""
import json

import duckdb
import pandas as pd
import pytest

from licita.spine import dq
from licita.spine.build import VIEWS_SQL, brand_pattern, ext_months, materialise_core

NIF = "Q0000001A"
FOLDER_BASE = dict(link=None, title="Suministro de reactivos", status="RES", buyer="Hospital Santa Ficticia", buyer_nif=NIF,
                   buyer_dir3="A0001", buyer_city="Villaejemplo", buyer_parents="Comunidad de Ejemplo", buyer_type="1",
                   is_health=True, contract_type="1", contract_subtype=None, cpvs="33696500", budget_estimated=None,
                   budget_total=None, budget_net=1000.0, nuts="ES999", planned_start=None, planned_end=None,
                   duration=12.0, duration_unit="MON", extension_desc=None, extension_period=None, n_lots=0,
                   lots_json=None, procedure="1", urgency=None, contracting_system=None, over_threshold=None,
                   submission_deadline=None, funding=None, attachments_json="[]", notice_cn="2024-01-01",
                   notice_pin=None, notice_award="2024-03-01", notice_formal=None, notices="", source_feed="643", source="a")
RESULT_BASE = dict(buyer_nif=NIF, source_feed="643", result_desc=None, award_date="2024-02-15", n_bids=2.0, n_sme_bids=None,
                   lower_bid=None, higher_bid=None, sme_awarded=None, contract_date=None, contract_start=None)


def _folder(entry, updated, folder_id="F1", **kw):
    return {**FOLDER_BASE, "entry_id": entry, "updated": updated, "folder_id": folder_id, **kw}


def _result(entry, updated, lot, winner, nif, code="9", amount=100.0, contract_id=None, folder_id="F1", **kw):
    return {**RESULT_BASE, "entry_id": entry, "updated": updated, "folder_id": folder_id, "lot_id": lot,
            "result_code": code, "winner": winner, "winner_nif": nif, "award_net": amount, "contract_id": contract_id, **kw}


@pytest.fixture
def con(tmp_path):
    folders = [
        _folder("e1", "2024-03-01T10:00:00+01:00"),                      # page 1: lots 1, 2 awarded, lot 4 deserted
        _folder("e1", "2024-06-01T10:00:00+02:00"),                      # page 2: lot 2 corrected, lot 3 new, lot 4 gone
        _folder("e2", "2024-04-01T10:00:00+02:00", folder_id="F2", status="ANUL"),   # annulled: latest snapshot only
        _folder("e2", "2024-02-01T10:00:00+01:00", folder_id="F2", status="ADJ"),
        # the same buyer under a DIR3-only key (feed 1044 style) and under a name-only key
        _folder("e3", "2024-05-01T10:00:00+02:00", folder_id="F3", buyer_nif=None, source_feed="1044"),
        _folder("e4", "2024-05-02T10:00:00+02:00", folder_id="F4", buyer_nif=None, buyer_dir3=None, source_feed="1044"),
        # a lower-case NIF with surrounding blanks, as PLACSP sometimes publishes it
        _folder("e5", "2024-05-03T10:00:00+02:00", folder_id="F5", buyer_nif=" q0000001a "),
        # award (code 8, no contract id) later republished as a formalisation (code 9, contract id), plus a
        # second call-off to the same winner listed in that later snapshot
        _folder("e6", "2024-06-01T10:00:00+02:00", folder_id="F6"),
        _folder("e6", "2024-08-01T10:00:00+02:00", folder_id="F6"),
    ]
    results = [
        _result("e1", "2024-03-01T10:00:00+01:00", "1", "Acme SA", "B1", amount=100),
        _result("e1", "2024-03-01T10:00:00+01:00", "2", "Beta SL", "B2", amount=200),
        _result("e1", "2024-03-01T10:00:00+01:00", "4", None, None, code="3", amount=None),
        _result("e1", "2024-06-01T10:00:00+02:00", "2", "BETA, S.L.", "b2", amount=210),
        _result("e1", "2024-06-01T10:00:00+02:00", "3", "Gamma SA", "B3", amount=300),
        _result("e2", "2024-02-01T10:00:00+01:00", None, "Acme SA", "B1", amount=50, folder_id="F2"),
        _result("e2", "2024-04-01T10:00:00+02:00", None, None, None, code="4", amount=None, folder_id="F2"),
        _result("e3", "2024-05-01T10:00:00+02:00", None, "Acme SA", "B1", amount=70, folder_id="F3", award_date=None,
                contract_date="2024-05-10"),
        _result("e4", "2024-05-02T10:00:00+02:00", None, "Acme SA", "B1", amount=80, folder_id="F4", award_date=None),
        _result("e5", "2024-05-03T10:00:00+02:00", None, "Acme SA", "B1", amount=90, folder_id="F5"),
        _result("e6", "2024-06-01T10:00:00+02:00", None, "Delta SA", "B4", code="8", amount=500, folder_id="F6"),
        _result("e6", "2024-08-01T10:00:00+02:00", None, "Delta SA", "B4", code="9", amount=500, contract_id="C1", folder_id="F6"),
        _result("e6", "2024-08-01T10:00:00+02:00", None, "Delta SA", "B4", code="9", amount=120, contract_id="C2", folder_id="F6"),
        # the same snapshot listing an award row and its formalisation row for one (lot, winner, contract)
        _result("e6", "2024-08-01T10:00:00+02:00", None, "Delta SA", "B4", code="8", amount=120, contract_id="C2", folder_id="F6"),
    ]
    # archive A holds page 1 of F1; archive B holds everything again plus page 2 (the overlap PLACSP produces)
    pd.DataFrame(folders[:1] + folders[2:]).to_parquet(tmp_path / "pA_folders.parquet", index=False)
    pd.DataFrame([r for r in results if r["updated"].startswith("2024-03")]).to_parquet(tmp_path / "pA_results.parquet", index=False)
    pd.DataFrame(folders).to_parquet(tmp_path / "pB_folders.parquet", index=False)
    pd.DataFrame(results).to_parquet(tmp_path / "pB_results.parquet", index=False)
    db = duckdb.connect()
    db.create_function("ext_months", ext_months, ["VARCHAR"], "DOUBLE", null_handling="special")
    stats = materialise_core(db, f"{tmp_path}/*")
    db.execute("create table brand_regex as select ? as pattern", [brand_pattern()])
    db.execute(VIEWS_SQL.read_text())
    db.execute("create table lots as select * from v_contract_terms")

    class Spine:                       # a connection plus the build stats (DuckDB connections take no attributes)
        execute = db.execute
        stats = None
    s = Spine()
    s.stats = stats
    return s


def test_overlapping_archives_do_not_duplicate(con):
    assert con.execute("select count(*) from snapshots").fetchone()[0] == 9
    assert con.execute("select count(*) from folders").fetchone()[0] == 6


def test_republished_award_replaces_earlier_row_and_keeps_sibling_contracts(con):
    rows = con.execute("select result_code, award_net, contract_id, from_latest_snapshot from results "
                       "where folder_id = 'F6' order by contract_id").fetchall()
    assert rows == [("9", 500.0, "C1", True), ("9", 120.0, "C2", True)]


def test_awards_union_across_pages_latest_version_wins(con):
    rows = con.execute("select lot_id, winner, winner_nif, award_net, from_latest_snapshot from results "
                       "where folder_id = 'F1' order by lot_id").fetchall()
    assert rows == [("1", "Acme SA", "B1", 100.0, False),       # page 1 only: recovered
                    ("2", "BETA, S.L.", "B2", 210.0, True),      # corrected on page 2: latest version
                    ("3", "Gamma SA", "B3", 300.0, True)]        # page 2 only
    assert con.stats["awards_recovered"] == 1


def test_stale_deserted_row_is_dropped_and_annulled_folder_uses_latest_only(con):
    assert con.execute("select count(*) from results where folder_id = 'F1' and result_code = '3'").fetchone()[0] == 0
    f2 = con.execute("select result_code, winner from results where folder_id = 'F2'").fetchall()
    assert f2 == [("4", None)]


def test_buyers_resolve_to_the_nif(con):
    keys = con.execute("select distinct buyer_key from folders").fetchall()
    assert keys == [(NIF,)]
    assert con.execute("select count(*) from folders where buyer_nif = ?", [NIF]).fetchone()[0] == 6
    assert con.stats["dir3_merged"] == 1 and con.stats["name_merged"] == 1


def test_award_date_falls_back_to_contract_then_notice(con):
    src = dict(con.execute("select folder_id, award_date_source from lots").fetchall())
    assert src["F1"] == "award_date" and src["F3"] == "contract_date" and src["F4"] == "notice_award"
    assert str(con.execute("select award_d from lots where folder_id = 'F4'").fetchone()[0]) == "2024-03-01"


def test_winner_nif_normalised_and_suppliers_canonical(con):
    assert con.execute("select distinct winner_nif from results where lot_id = '2'").fetchall() == [("B2",)]
    con.execute("""create table suppliers as
        with names as (select winner_nif, winner, count(*) as c from lots where winner_nif is not null group by 1, 2),
             canon as (select winner_nif, arg_max(winner, c) as canonical_name, count(*) as n_spellings from names group by 1)
        select l.winner_nif, c.canonical_name, c.n_spellings, count(*) as n_awards from lots l join canon c using (winner_nif) group by all""")
    acme = con.execute("select canonical_name, n_awards from suppliers where winner_nif = 'B1'").fetchone()
    assert acme == ("Acme SA", 4)


def test_quality_gate_runs_on_the_mini_spine(con):
    from datetime import date
    checks = dq.run(con, date(2024, 7, 1), demo=True)
    names = {c.name for c in checks}
    assert {"folders_unique", "awards_retained", "award_key_unique", "award_single_version", "award_date_present"} <= names
    assert not dq.failures(checks), [c for c in checks if not c.passed]
    assert "| folders_unique | hard | pass |" in dq.to_markdown(checks)


def test_continuity_detects_gaps_and_dips():
    months = [("643", f"{y}-{m:02d}", 100) for y in (2022, 2023) for m in range(1, 13)]
    months = [r for r in months if r[1] != "2023-04"]                 # one empty month
    months.append(("643", "2024-01", 5))                              # one collapse
    gaps, dips, span = dq.continuity(months)
    assert gaps == ["643 2023-04"] and dips == ["643 2024-01 (5)"] and span
    assert dq.continuity([("643", "2024-01", 3)]) == ([], [], False)   # too short to judge
    assert dq.continuity(months, last_month="2023-12")[1] == []          # the month after the cut-off is ignored


def test_lots_json_budget_still_wins_over_folder_budget(con):
    # guards the 2026-10-09 rule: a lot inherits the folder budget only when it is the folder's only award
    assert con.execute("select count(*) from lots where folder_id = 'F1' and lot_budget is not null").fetchone()[0] == 0
    assert con.execute("select lot_budget from lots where folder_id = 'F5'").fetchone()[0] == 1000.0
    assert json.loads(FOLDER_BASE["attachments_json"]) == []


def test_stability_checks_against_a_previous_spine(con, tmp_path):
    """The gate attaches the live spine and compares buyer ranks and lot counts; no file → no checks;
    an incompatible file → one informative check, never an error."""
    import duckdb as ddb
    assert dq.stability(con, None) == []
    bad = tmp_path / "bad.duckdb"
    with ddb.connect(str(bad)) as p:
        p.execute("create table lots (i int)")
    assert [c.name for c in dq.stability(con, bad)] == ["metric_stability"]
    pq = tmp_path / "lots.parquet"
    con.execute(f"copy lots to '{pq}' (format parquet)")
    prev = tmp_path / "prev.duckdb"
    with ddb.connect(str(prev)) as p:
        p.execute(f"create table lots as select * from read_parquet('{pq}')")
    checks = {c.name: c for c in dq.stability(con, prev)}
    assert checks["metric_stability"].passed and checks["lot_count_drift"].passed
    assert "+0.0%" in checks["lot_count_drift"].value
