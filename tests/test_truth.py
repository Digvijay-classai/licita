"""Ground truth and outcomes: validation, the radar override, matcher precision, the CLI."""
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from licita.errors import LicitaError
from licita.profile import load_profile
from licita.spine import truth
from licita.spine.radar import apply_truth, build_radar, matcher_precision

TPL = Path(truth.TEMPLATES)


def _dir(tmp_path, ext_rows=None, outcome_rows=None):
    d = tmp_path / "truth"
    d.mkdir()
    if ext_rows is not None:
        (d / "HOSP_A_extensions.csv").write_text(
            "# comment\n" + ",".join(truth.TRUTH_COLUMNS) + "\n" + "\n".join(ext_rows) + "\n")
    if outcome_rows is not None:
        (d / "outcomes.csv").write_text(",".join(truth.OUTCOME_COLUMNS) + "\n" + "\n".join(outcome_rows) + "\n")
    return d


# ---------------------------------------------------------------- loading and validation

def test_templates_load_clean(tmp_path):
    d = tmp_path / "truth"
    d.mkdir()
    (d / "HOSP_A_extensions.csv").write_text((TPL / "ground_truth_extensions.csv").read_text())
    (d / "outcomes.csv").write_text((TPL / "outcomes.csv").read_text())
    t = truth.load_truth(d)
    assert list(t.columns) == truth.TRUTH_COLUMNS and len(t) == 1
    assert t.status[0] == "extended" and t.extended_until[0] == date(2027, 6, 30)
    assert t.successor_folder_id[0] is None            # empty cell -> None, not ""
    o = truth.load_outcomes(d)
    assert len(o) == 1 and o.value_eur[0] == 140000 and pd.isna(o.bids[0])


def test_missing_dir_gives_empty_frames(tmp_path):
    assert truth.load_truth(tmp_path / "nope").empty
    assert truth.load_outcomes(tmp_path / "nope").empty
    assert list(truth.load_truth(tmp_path / "nope").columns) == truth.TRUTH_COLUMNS


def test_invalid_rows_name_the_line_and_the_fix(tmp_path):
    d = _dir(tmp_path, [
        "HOSP_A,F1,extended,,,note,head,2026-10-20",            # extended without extended_until
        "HOSP_A,F2,renewed,,,note,head,2026-10-20",             # unknown status
        "HOSP_A,F3,replaced,F9,,note,head,20/10/2026",          # bad date
        "HOSP_A,F3,lapsed,,,note,head,2026-10-21",              # duplicate key
        ",F4,lapsed,,,note,,2026-10-21",                        # empty required fields
    ])
    with pytest.raises(LicitaError) as e:
        truth.load_truth(d)
    msg = str(e.value)
    assert "invalid row(s)" in msg and "cause:" in msg and "fix:" in msg
    assert "HOSP_A_extensions.csv:2: status extended needs extended_until" in msg
    assert ":3: status 'renewed' not in" in msg and ":4: recorded_on '20/10/2026'" in msg
    assert "(HOSP_A, F3) appears 2 times" in msg and ":6: buyer_key is empty" in msg and ":6: source is empty" in msg


def test_invalid_outcomes(tmp_path):
    d = _dir(tmp_path, outcome_rows=["2026-11-05,HOSP_A,,party,abc,,n,head"])
    with pytest.raises(LicitaError) as e:
        truth.load_outcomes(d)
    assert "event 'party' not in" in str(e.value) and "value_eur 'abc' is not a number" in str(e.value)


def test_write_table_typed_even_when_empty():
    con = duckdb.connect()
    assert truth.write_table(con, truth.load_truth(Path("/nonexistent")), "truth") == 0
    assert truth.write_table(con, truth.load_outcomes(Path("/nonexistent")), "outcomes") == 0
    assert con.execute("select count(*) from truth").fetchone()[0] == 0
    cols = dict(con.execute("select column_name, data_type from information_schema.columns "
                            "where table_name = 'truth'").fetchall())
    assert cols["extended_until"] == "DATE" and cols["recorded_on"] == "DATE"
    df = pd.DataFrame([{"buyer_key": "HOSP_A", "folder_id": "F1", "status": "extended", "successor_folder_id": None,
                        "extended_until": date(2027, 1, 1), "note": None, "source": "head",
                        "recorded_on": date(2026, 10, 20)}])
    assert truth.write_table(con, df, "truth") == 1
    assert con.execute("select status, extended_until from truth").fetchone() == ("extended", date(2027, 1, 1))


def test_outcome_summary(tmp_path):
    d = _dir(tmp_path, outcome_rows=[
        "2025-03-05,HOSP_A,F1,memo_delivered,140000,,,team",
        "2025-03-20,HOSP_A,F2,memo_delivered,50000,,,team",
        "2025-04-02,HOSP_A,F1,file_opened_before_start_by,140000,,,team",
        "2025-04-09,HOSP_B,,market_consultation_run,,,,team",
    ])
    s = truth.summary(truth.load_outcomes(d))
    assert list(s.columns) == ["buyer_key", "month", "event", "n", "value_eur"]
    nov = s[(s.month == "2025-03") & (s.event == "memo_delivered")].iloc[0]
    assert nov.n == 2 and nov.value_eur == 190000
    assert s[s.buyer_key == "HOSP_B"].value_eur.iloc[0] == 0
    assert truth.summary(truth.load_outcomes(tmp_path / "nope")).empty


# ---------------------------------------------------------------- the radar override

AS_OF = date(2026, 10, 10)


def _radar(rows):
    base = {"hospital": "HOSP_A", "buyer_nif": "Q0000001A", "successor_status": "no_successor", "match_confidence": 0.9,
            "successor_folder": None, "end_initial": date(2026, 6, 1), "months_to_end": -4.3, "flag": "verify"}
    return pd.DataFrame([{**base, **r} for r in rows])


def _truth(rows):
    base = {"buyer_key": "HOSP_A", "successor_folder_id": None, "extended_until": None, "note": None,
            "source": "head", "recorded_on": date(2026, 10, 20)}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_apply_truth_each_status():
    radar = _radar([
        {"folder_id": "EXT"}, {"folder_id": "EXT_OLD"}, {"folder_id": "PEND"}, {"folder_id": "REPL"},
        {"folder_id": "FWK", "successor_status": "absorbed_by_framework", "flag": "covered"},
        {"folder_id": "PROG"}, {"folder_id": "LAPSED"},
        {"folder_id": "LAPSED_FUTURE", "end_initial": date(2027, 3, 1), "flag": "due_6m"},
        {"folder_id": "GONE"}, {"folder_id": "NOTRUTH"},
        {"folder_id": "BYNIF", "hospital": "HOSP_B", "buyer_nif": "Q0000002B"},
    ])
    t = _truth([
        {"folder_id": "EXT", "status": "extended", "extended_until": date(2027, 4, 10)},
        {"folder_id": "EXT_OLD", "status": "extended", "extended_until": date(2026, 9, 1)},
        {"folder_id": "PEND", "status": "extension_pending"},
        {"folder_id": "REPL", "status": "replaced", "successor_folder_id": "NEW1"},
        {"folder_id": "FWK", "status": "replaced_by_framework"},
        {"folder_id": "PROG", "status": "in_progress"},
        {"folder_id": "LAPSED", "status": "lapsed"},
        {"folder_id": "LAPSED_FUTURE", "status": "lapsed"},
        {"folder_id": "GONE", "status": "ended_no_need"},
        {"folder_id": "BYNIF", "status": "replaced", "buyer_key": "Q0000002B"},
    ])
    out = apply_truth(radar, t, AS_OF).set_index("folder_id")
    assert out.loc["EXT", "flag"] == "covered" and out.loc["EXT", "successor_status"] == "extended_confirmed"
    assert out.loc["EXT", "months_to_end"] == pytest.approx((date(2027, 4, 10) - AS_OF).days / 30.4, abs=0.1)
    assert out.loc["EXT_OLD", "flag"] == "verify" and out.loc["EXT_OLD", "successor_status"] == "extension_expired"
    assert out.loc["PEND", "flag"] == "verify" and out.loc["PEND", "successor_status"] == "extension_pending"
    assert out.loc["REPL", "flag"] == "covered" and out.loc["REPL", "successor_folder"] == "NEW1"
    assert out.loc["FWK", "successor_status"] == "absorbed_by_framework_confirmed"
    assert out.loc["PROG", "flag"] == "covered"
    assert out.loc["LAPSED", "flag"] == "start_now" and out.loc["LAPSED", "successor_status"] == "lapsed_confirmed"
    assert out.loc["LAPSED_FUTURE", "flag"] == "due_6m"              # not ended yet: computed flag kept
    assert out.loc["GONE", "flag"] == "ok" and out.loc["GONE", "successor_status"] == "ended_no_need"
    assert out.loc["NOTRUTH", "truth_status"] is None and out.loc["NOTRUTH", "flag"] == "verify"
    assert out.loc["BYNIF", "flag"] == "covered"                      # matched on the NIF, not the profile key
    assert out.loc["EXT", "heuristic_status"] == "no_successor" and out.loc["EXT", "heuristic_flag"] == "verify"
    assert out.loc["EXT", "truth_source"] == "head" and out.loc["EXT", "match_confidence"] == 1.0


def test_apply_truth_without_truth_keeps_everything_and_adds_columns():
    radar = _radar([{"folder_id": "A"}])
    out = apply_truth(radar, None, AS_OF)
    assert out.flag[0] == "verify" and out.truth_status[0] is None and out.heuristic_flag[0] == "verify"
    empty = apply_truth(radar.iloc[0:0], None, AS_OF)
    assert "truth_status" in empty.columns and empty.empty


def test_matcher_precision_hand_computed():
    radar = _radar([
        {"folder_id": "1", "successor_status": "extended"},        # truth extended   -> tp
        {"folder_id": "2", "successor_status": "extended"},        # truth replaced   -> fp (ext), fn (repl)
        {"folder_id": "3", "successor_status": "no_successor"},    # truth extended   -> fn (ext)
        {"folder_id": "4", "successor_status": "replaced_late"},   # truth replaced   -> tp (repl)
        {"folder_id": "5", "successor_status": "replaced"},        # no truth         -> ignored
    ])
    t = _truth([{"folder_id": "1", "status": "extended"}, {"folder_id": "2", "status": "replaced"},
                {"folder_id": "3", "status": "extended"}, {"folder_id": "4", "status": "replaced"}])
    p = matcher_precision(apply_truth(radar, t, AS_OF))
    assert p["n_truth"] == 4 and p["agreement"] == 0.5
    assert p["extended"] == {"tp": 1, "fp": 1, "fn": 1, "precision": 0.5, "recall": 0.5}
    assert p["replaced"] == {"tp": 1, "fp": 0, "fn": 1, "precision": 1.0, "recall": 0.5}
    assert p["framework"]["precision"] is None
    assert matcher_precision(pd.DataFrame({"flag": []})) == {"n_truth": 0}


# ---------------------------------------------------------------- build_radar on a mini spine

def _mini_spine():
    con = duckdb.connect()
    con.execute("""create table lots (buyer_nif varchar, folder_id varchar, title varchar, cpvs varchar, cpv2 varchar,
        award_d date, end_initial date, end_max date, n_lots int, award_net double, n_bids double, procedure varchar,
        link varchar, brand_in_title boolean, annual_budget double, winner varchar, contract_type varchar,
        duration_months double)""")
    con.execute("""insert into lots values
        ('Q0000001A', 'OLD', 'Servicio de jardinería', '77310000', '77', date '2024-01-10', date '2026-01-10',
         date '2027-01-10', 0, 300000, 1, '1', 'x', false, 150000, 'Jardines Ficticios', '2', 24),
        ('Q0000001A', 'GAS', 'Suministro de papel de oficina', '30197000', '30', date '2024-03-01', date '2026-03-01',
         date '2027-03-01', 0, 120000, 1, '1', 'y', false, 60000, 'Papeles Ficticios', '1', 24)""")
    con.execute("""create table folders (buyer_key varchar, buyer_nif varchar, buyer varchar, folder_id varchar,
                   title varchar, cpvs varchar, status varchar, procedure varchar, n_lots int)""")
    con.execute("create table snapshots (buyer_key varchar, folder_id varchar, notice_pin varchar, "
                "notice_cn varchar, updated varchar)")
    for fid, title, status in [("OLD", "Servicio de lavado de ropa", "RES"), ("GAS", "Suministro de gases medicinales", "RES")]:
        con.execute("insert into folders values ('Q0000001A', 'Q0000001A', 'Hospital Santa Ficticia', ?, ?, '98310000', ?, '1', 0)",
                    [fid, title, status])
        con.execute("insert into snapshots values ('Q0000001A', ?, '2024-01-01', null, null)", [fid])
    con.execute("create table lead_times (cpv2 varchar, p80_days double)")
    con.execute("insert into lead_times values ('98', 120), ('24', 120)")
    return con


def test_build_radar_without_truth_table_still_works():
    con = _mini_spine()
    df = build_radar(con, {"HOSP_A": load_profile().buyer("HOSP_A")}, AS_OF)
    assert set(df.folder_id) == {"OLD", "GAS"}
    assert set(df.flag) == {"verify"} and df.truth_status.isna().all()
    assert con.execute("select count(*) from radar").fetchone()[0] == 2
    assert "heuristic_flag" in {c[0] for c in con.execute("describe radar").fetchall()}


def test_build_radar_reads_truth_table_from_the_spine():
    con = _mini_spine()
    truth.write_table(con, _truth([{"folder_id": "OLD", "status": "extended", "extended_until": date(2027, 1, 10)}]), "truth")
    df = build_radar(con, {"HOSP_A": load_profile().buyer("HOSP_A")}, AS_OF).set_index("folder_id")
    assert df.loc["OLD", "flag"] == "covered" and df.loc["OLD", "successor_status"] == "extended_confirmed"
    assert df.loc["GAS", "flag"] == "verify" and df.loc["GAS", "truth_status"] is None
    row = con.execute("select flag, truth_status, heuristic_status from radar where folder_id = 'OLD'").fetchone()
    assert row == ("covered", "extended", "no_successor")
    p = matcher_precision(df.reset_index())
    assert p["n_truth"] == 1 and p["extended"]["recall"] == 0.0


def test_build_radar_truth_argument_wins_over_table():
    con = _mini_spine()
    truth.write_table(con, truth.load_truth(Path("/nonexistent")), "truth")
    explicit = _truth([{"folder_id": "GAS", "status": "ended_no_need"}])
    df = build_radar(con, {"HOSP_A": load_profile().buyer("HOSP_A")}, AS_OF, truth=explicit).set_index("folder_id")
    assert df.loc["GAS", "flag"] == "ok"


# ---------------------------------------------------------------- CLI

def test_cli_validate_template_outcomes(tmp_path, capsys):
    d = _dir(tmp_path, ["HOSP_A,F1,replaced,F2,,n,head,2026-10-20", "HOSP_A,F3,extended,,2027-01-01,n,head,2026-10-20"],
             ["2026-11-05,HOSP_A,F1,memo_delivered,1000,,n,head"])
    assert truth.main(["--dir", str(d), "validate"]) == 0
    out = capsys.readouterr().out
    assert "extended" in out and "replaced" in out and "outcomes: 1 row(s)" in out
    assert truth.main(["--dir", str(d), "template", "extensions"]) == 0
    assert (d / "BUYER_extensions.csv").read_text() == (TPL / "ground_truth_extensions.csv").read_text()
    assert truth.main(["--dir", str(d), "template", "extensions"]) == 1       # never overwrites
    assert truth.main(["--dir", str(d), "outcomes"]) == 0
    assert "memo_delivered" in capsys.readouterr().out


def test_cli_validate_reports_problems_and_fails(tmp_path, capsys):
    d = _dir(tmp_path, ["HOSP_A,F1,bogus,,,n,head,2026-10-20"])
    assert truth.main(["--dir", str(d), "validate"]) == 1
    assert "status 'bogus' not in" in capsys.readouterr().err


def test_cli_import_previews_against_a_spine(tmp_path, capsys):
    d = _dir(tmp_path, ["HOSP_A,OLD,extended,,2027-01-10,n,head,2026-10-20", "HOSP_A,UNKNOWN,lapsed,,,n,head,2026-10-20"])
    spine = tmp_path / "spine.duckdb"
    with duckdb.connect(str(spine)) as con:
        con.register("r", _radar([{"folder_id": "OLD"}, {"folder_id": "GAS"}]))
        con.execute("create table radar as select * from r")
        con.execute("create table meta as select date '2026-10-10' as as_of")
    assert truth.main(["--dir", str(d), "import", "--spine", str(spine)]) == 0
    out = capsys.readouterr().out
    assert "2 truth row(s); 1 match a radar contract (1 name folders the radar does not list)" in out
    assert "verify" in out and "covered" in out and "preview only" in out
    assert truth.main(["--dir", str(d), "import", "--spine", str(tmp_path / "missing.duckdb")]) == 1
