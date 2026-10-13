"""Deserted-lot tracker on a mini spine: budgets, dates, republication detection and outcomes."""
import json

import duckdb
import pandas as pd

from licita.spine import deserted

LOTS = json.dumps([{"lot_id": "1", "budget_net": 10_000.0}, {"lot_id": "2", "budget_net": 30_000.0}])


def _folder(fid, title, cpvs, updated, n_lots=0, lots_json=None, budget=None, notice_award=None, notice_cn=None):
    return dict(buyer_key="A", buyer_nif="A", folder_id=fid, title=title, cpvs=cpvs, n_lots=n_lots,
                lots_json=lots_json, budget_net=budget, notice_award=notice_award, notice_cn=notice_cn,
                notice_pin=None, updated=updated, procedure="1", source_feed="643")


def _result(fid, lot, code, award_date=None, n_bids=None):
    return dict(buyer_key="A", folder_id=fid, lot_id=lot, result_code=code, award_date=award_date,
                contract_date=None, updated="2024-01-01T00:00:00", n_bids=n_bids)


def _mini():
    con = duckdb.connect()
    folders = pd.DataFrame([
        # two-lot tender: lot 1 deserted, lot 2 awarded; republished three months later and awarded
        _folder("D1", "Suministro de material de papelería y consumibles de impresión", "30190000",
                "2024-03-01T00:00:00", n_lots=2, lots_json=LOTS, notice_award="2024-03-01"),
        _folder("D2", "Suministro de material de papelería y consumibles de impresión (2ª licitación)", "30190000",
                "2024-07-01T00:00:00", n_lots=0, budget=12_000.0, notice_cn="2024-06-01"),
        # single-lot works tender deserted, never republished
        _folder("W1", "Obras de reforma de la cocina", "45210000", "2024-05-01T00:00:00", n_lots=0,
                budget=200_000.0, notice_award="2024-05-10"),
        # unrelated later tender of the same buyer: must not be taken as a republication of W1
        _folder("X1", "Servicio de vigilancia y seguridad", "79710000", "2024-09-01T00:00:00", n_lots=0,
                budget=50_000.0, notice_cn="2024-08-01"),
    ])
    results = pd.DataFrame([
        _result("D1", "1", "3", award_date="2024-03-01"),
        _result("D1", "2", "9", award_date="2024-03-01", n_bids=2.0),
        _result("D2", None, "9", award_date="2024-08-15", n_bids=3.0),
        _result("W1", None, "3", award_date=None),
        _result("X1", None, "9", award_date="2024-10-01", n_bids=4.0),
    ])
    lots = pd.DataFrame([
        dict(cpv4="3019", is_health=True, is_open=True, n_bids=1.0), dict(cpv4="3019", is_health=True, is_open=True, n_bids=3.0),
        dict(cpv4="3019", is_health=True, is_open=True, n_bids=3.0), dict(cpv4="4521", is_health=True, is_open=True, n_bids=5.0),
    ])
    for name, df in (("folders", folders), ("results", results), ("lots", lots)):
        con.register(f"{name}_df", df)
        con.execute(f"create table {name} as select * from {name}_df")
    con.execute("create table snapshots as select buyer_key, folder_id, title, cpvs, notice_cn, notice_pin, updated from folders")
    return con


def test_track_budget_date_republication_and_market():
    df = deserted.track(_mini()).set_index("folder_id")
    d1 = df.loc["D1"]
    assert d1.lot_id == "1" and d1.budget == 10_000.0 and str(d1.deserted_date)[:10] == "2024-03-01"
    assert d1.republished_folder == "D2" and d1.outcome == "awarded" and d1.bids_after == 3.0
    assert d1.match_basis == "title" and d1.similarity >= 0.5
    assert d1.market_median_bids == 3.0 and d1.market_single_bid_rate == 1 / 3
    w1 = df.loc["W1"]
    assert w1.budget == 200_000.0 and str(w1.deserted_date)[:10] == "2024-05-10"   # award-notice fallback
    assert pd.isna(w1.republished_folder) and w1.outcome == "not_republished"
    assert set(df.index) == {"D1", "W1"}                                           # awarded lots are not rows


def test_summary_per_buyer_year():
    s = deserted.summary(deserted.track(_mini()))
    row = s[(s.buyer_key == "A") & (s.year == 2024)].iloc[0]
    assert row.n_deserted == 2 and row.budget == 210_000.0
    assert row.n_republished == 1 and row.n_awarded_after == 1 and row.n_deserted_again == 0


def test_empty_spine_gives_empty_frames():
    con = duckdb.connect()
    con.execute("create table folders (buyer_key varchar, buyer_nif varchar, folder_id varchar, title varchar, cpvs varchar, "
                "n_lots int, lots_json varchar, budget_net double, notice_award varchar, notice_cn varchar, notice_pin varchar, updated varchar, "
                "procedure varchar, source_feed varchar)")
    con.execute("create table results (buyer_key varchar, folder_id varchar, lot_id varchar, result_code varchar, "
                "award_date varchar, contract_date varchar, updated varchar, n_bids double)")
    df = deserted.track(con)
    assert df.empty and deserted.summary(df).empty
