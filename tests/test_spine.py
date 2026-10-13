import json
import re

import duckdb
import numpy as np
import pandas as pd
import pytest

from licita.spine.build import VIEWS_SQL, bidder_value, brand_pattern, ext_months


@pytest.mark.parametrize("text,months", [
    ("Prorrogable por 2 años", 24),
    ("hasta un máximo de 24 meses", 24),
    ("prórroga de una anualidad", None),          # no number: we don't guess
    ("1 anualidad adicional", 12),
    ("90 días", 90 / 30.4),
    ("Se podrán acordar prórrogas de 1,5 años", 18),
    ("", None),
    (None, None),
    ("sin prórroga", None),
    ("3 AÑOS", 36),
])
def test_ext_months(text, months):
    got = ext_months(text)
    assert (got is None and months is None) or got == pytest.approx(months)


@pytest.mark.parametrize("title,hit", [
    ("Mantenimiento de la resonancia magnética GENERAL ELECTRIC", True),
    ("Mantenimiento equipos de la marca Philips", True),
    ("Suministro de material para el sistema Da Vinci", True),
    ("Servicio de limpieza del hospital", False),
    ("Suministro de B. Braun bombas", True),
    ("Suministro de bandas elásticas", False),   # 'bd' must not match inside words
])
def test_brand_pattern(title, hit):
    assert bool(re.search(brand_pattern(), title.lower())) is hit


def test_bidder_value_pooled_and_ci():
    rng = np.random.default_rng(0)
    rows = []
    for k, base in [(1, 0.0), (2, 0.08), (3, 0.11)]:
        for _ in range(200):
            rows.append({"cpv2": "50", "n_bids": k, "discount": base + rng.normal(0, 0.02)})
    bv = bidder_value(pd.DataFrame(rows), n_boot=100)
    nat = bv[(bv.cpv2 == "**") & (bv.k == 1)].iloc[0]
    assert nat.delta == pytest.approx(0.08, abs=0.01)
    assert nat.ci_lo < nat.delta < nat.ci_hi
    assert set(bv.k) == {1, 2}


def test_bidder_value_needs_min_n():
    df = pd.DataFrame({"cpv2": ["50"] * 20, "n_bids": [1] * 10 + [2] * 10, "discount": [0.0] * 10 + [0.1] * 10})
    assert bidder_value(df, min_n=15).empty


def _mini_spine():
    con = duckdb.connect()
    con.create_function("ext_months", ext_months, ["VARCHAR"], "DOUBLE", null_handling="special")
    lots = json.dumps([{"lot_id": "1", "budget_net": 100.0}, {"lot_id": "2", "budget_net": 300.0}])
    folders = pd.DataFrame([
        # single-lot open tender, 12 months, 2 bids, 10% discount
        dict(buyer_nif="A", folder_id="F1", title="Mantenimiento equipos Siemens", buyer="Hosp A", buyer_dir3=None,
             buyer_parents="", is_health=True, source_feed="643", cpvs="50421000", procedure="1",
             contract_type="2", n_lots=0, nuts="ES999", link="x", notice_cn="2024-01-01", notice_award=None,
             submission_deadline="2024-01-21", extension_desc=None, extension_period="1 año",
             attachments_json="[]", lots_json=None, budget_net=1000.0, duration=12.0, duration_unit="MON",
             planned_start="2024-03-01", planned_end=None),
        # two-lot negotiated tender
        dict(buyer_nif="A", folder_id="F2", title="Suministro de gasas", buyer="Hosp A", buyer_dir3=None,
             buyer_parents="", is_health=True, source_feed="643", cpvs="33141000", procedure="3",
             contract_type="1", n_lots=2, nuts="ES999", link="y", notice_cn=None, notice_award="2024-05-03",
             submission_deadline=None,
             extension_desc=None, extension_period=None, attachments_json="[]", lots_json=lots,
             budget_net=400.0, duration=2.0, duration_unit="ANN", planned_start=None, planned_end=None),
    ])
    results = pd.DataFrame([
        dict(buyer_nif="A", folder_id="F1", lot_id=None, result_code="8", award_date="2024-02-15", n_bids=2.0,
             award_net=900.0, contract_date=None),
        dict(buyer_nif="A", folder_id="F2", lot_id="1", result_code="9", award_date="2024-05-01", n_bids=1.0,
             award_net=100.0, contract_date="2024-05-10"),
        dict(buyer_nif="A", folder_id="F2", lot_id="2", result_code="9", award_date="2024-05-01", n_bids=1.0,
             award_net=240.0, contract_date="2024-05-10"),
        dict(buyer_nif="A", folder_id="F2", lot_id="3", result_code="3", award_date="2024-05-01", n_bids=0.0,
             award_net=None, contract_date=None),   # deserted lot: must be excluded
    ])
    con.register("folders_df", folders)
    con.register("results_df", results)
    con.execute("create table folders as select *, buyer_nif as buyer_key from folders_df")
    con.execute("create table results as select *, buyer_nif as buyer_key from results_df")
    con.execute("create table brand_regex as select ? as pattern", [brand_pattern()])
    con.execute(VIEWS_SQL.read_text())
    return con


def test_views_hand_computed():
    con = _mini_spine()
    df = con.execute("select folder_id, lot_id, lot_budget, discount, band, is_open, brand_in_title, "
                     "duration_months, deadline_days, end_initial, end_max from v_contract_terms "
                     "order by folder_id, lot_id").df()
    assert len(df) == 3                                    # deserted lot excluded
    f1 = df[df.folder_id == "F1"].iloc[0]
    assert f1.lot_budget == 1000 and f1.discount == pytest.approx(0.10)
    assert f1.is_open and f1.brand_in_title and f1.band == "a<15k"
    assert f1.deadline_days == 20
    assert str(f1.end_initial)[:10] == "2025-03-01"         # planned start + 12 months
    assert str(f1.end_max)[:10] == "2026-03-01"             # + 1-year extension (informational)
    l2 = df[(df.folder_id == "F2") & (df.lot_id == "2")].iloc[0]
    assert l2.lot_budget == 300 and l2.discount == pytest.approx(0.20)   # lot budget, not folder budget
    assert not l2.is_open and l2.duration_months == 24
