"""Single-bid reason buckets: the pure rules on hand-made rows, then classify/summary on a mini spine."""
import duckdb
import pandas as pd
import pytest

from licita.spine import reasons


def _row(title, winner="Acme SA", cpv4="5042", brand=False, deadline=30, budget=50_000.0):
    return {"title": title, "winner": winner, "cpv4": cpv4, "brand_in_title": brand, "deadline_days": deadline,
            "lot_budget": budget}


@pytest.mark.parametrize("row,peer,expected,first_signal", [
    (_row("Mantenimiento de la resonancia magnética Siemens", "Siemens Healthcare SL", brand=True), (0.3, 50),
     "structural", "oem_incumbent"),
    (_row("Suministro de material de curas"), (0.85, 40), "structural", "peer_exclusive"),
    (_row("Suministro en exclusividad de reactivos"), (0.3, 40), "structural", "exclusivity_cue"),
    (_row("Medicamento con principio activo nusinersen", cpv4="3369"), (0.3, 40), "structural",
     "medicine_active_principle"),
    (_row("Servicios postales", cpv4="6411"), None, "structural", "postal_universal"),
    (_row("Prórroga del expediente 12/2020 limpieza"), (0.3, 40), "earlier_decision", "extension"),
    (_row("Suministro basado en acuerdo marco 4/2022"), None, "earlier_decision", "framework"),
    (_row("Fungible compatible con el sistema instalado"), None, "earlier_decision", "compatibility_cue"),
    (_row("Prótesis de cadera Stryker", winner="Distribuidora SL", cpv4="3318", brand=True), None,
     "earlier_decision", "brand_consumable"),
    (_row("Servicio de lavandería", cpv4="9830"), (0.6, 40), "addressable", "thin_market"),
    (_row("Suministro de guantes"), (0.2, 40), "addressable", "competitive_market"),
])
def test_bucket_rules(row, peer, expected, first_signal):
    rate, n = peer if peer else (None, None)
    bucket, conf, signals, lever = reasons.bucket_for(row, rate, n)
    assert bucket == expected
    assert signals.split(",")[0] == first_signal
    assert lever == reasons.LEVERS[first_signal]


def test_structural_beats_extension_and_confidence_counts_signals():
    row = _row("Prórroga mantenimiento TAC Siemens", "Siemens Healthcare SL", brand=True)
    bucket, conf, signals, _ = reasons.bucket_for(row, 0.9, 30)
    assert bucket == "structural" and conf == 0.9                      # oem + peer_exclusive agree
    assert "extension" in signals.split(",")
    bucket, conf, _, _ = reasons.bucket_for(_row("Suministro de vendas"), 0.9, 30)
    assert bucket == "structural" and conf == 0.7                      # one strong signal


def test_peer_cell_too_small_is_ignored():
    bucket, conf, signals, lever = reasons.bucket_for(_row("Suministro de vendas", cpv4="3314"), 0.95, 5)
    assert bucket == "addressable" and conf == 0.5 and signals == "" and lever == reasons.LEVERS["none"]


def test_addressable_subsignals():
    bucket, conf, signals, _ = reasons.bucket_for(_row("Obra menor", cpv4="4521", deadline=10, budget=900_000.0), 0.6, 40)
    assert bucket == "addressable" and conf == 0.5
    assert signals == "thin_market,short_deadline,large_lot"
    assert reasons.bucket_for(_row("Guantes", cpv4="3314"), 0.1, 40)[1] == 0.7      # competitive elsewhere: a real signal


def _mini():
    con = duckdb.connect()
    lots = pd.DataFrame([
        dict(buyer_key="A", buyer_nif="A", folder_id="F1", lot_id=None, title_clean="Mantenimiento TAC Siemens",
             winner="Siemens Healthcare SL", cpv4="5042", band="b<100k", lot_budget=80_000.0, award_net=80_000.0,
             award_d=pd.Timestamp("2024-01-01"), deadline_days=25, brand_in_title=True, is_open=True, n_bids=1.0),
        dict(buyer_key="A", buyer_nif="A", folder_id="F2", lot_id="1", title_clean="Guantes de nitrilo",
             winner="Guantes SL", cpv4="3314", band="b<100k", lot_budget=20_000.0, award_net=20_000.0,
             award_d=pd.Timestamp("2024-02-01"), deadline_days=30, brand_in_title=False, is_open=True, n_bids=1.0),
        dict(buyer_key="A", buyer_nif="A", folder_id="F2", lot_id="2", title_clean="Guantes de látex",
             winner="Guantes SL", cpv4="3314", band="b<100k", lot_budget=20_000.0, award_net=20_000.0,
             award_d=pd.Timestamp("2024-02-01"), deadline_days=30, brand_in_title=False, is_open=True, n_bids=2.0),
        # two awards in one folder with no lot id: the grain that broke a join-based summary
        dict(buyer_key="B", buyer_nif="B", folder_id="F3", lot_id=None, title_clean="Prórroga limpieza",
             winner="Limpia SA", cpv4="9091", band="c<500k", lot_budget=None, award_net=100_000.0,
             award_d=pd.Timestamp("2024-03-01"), deadline_days=None, brand_in_title=False, is_open=True, n_bids=1.0),
        dict(buyer_key="B", buyer_nif="B", folder_id="F3", lot_id=None, title_clean="Prórroga limpieza",
             winner="Otra SA", cpv4="9091", band="c<500k", lot_budget=None, award_net=50_000.0,
             award_d=pd.Timestamp("2024-03-01"), deadline_days=None, brand_in_title=False, is_open=True, n_bids=1.0),
    ])
    peers = pd.DataFrame([dict(cpv4="5042", band="b<100k", n=40, single_bid_rate=0.85, median_bids=1.0, median_discount=0.0),
                          dict(cpv4="3314", band="b<100k", n=40, single_bid_rate=0.2, median_bids=3.0, median_discount=0.1)])
    con.register("lots_df", lots)
    con.register("peers_df", peers)
    con.execute("create table lots as select * from lots_df")
    con.execute("create table peers as select * from peers_df")
    return con


def test_classify_and_summary_on_mini_spine():
    con = _mini()
    df = reasons.classify(con)
    assert len(df) == 4                                           # the 2-bid lot is out
    by = dict(zip(df.folder_id + "/" + df.lot_id.fillna(""), df.reason_bucket, strict=True))
    assert by == {"F1/": "structural", "F2/1": "addressable", "F3/": "earlier_decision"} or \
        by["F1/"] == "structural" and by["F2/1"] == "addressable" and by["F3/"] == "earlier_decision"
    assert df[df.folder_id == "F1"].reason_confidence.iloc[0] == 0.9
    s = reasons.summary(df, con)
    b = s[s.buyer_key == "B"].iloc[0]
    assert b.n_lots == 2 and b.award_net == 150_000.0 and b.value_share == 1.0
    a = s[s.buyer_key == "A"].set_index("reason_bucket")
    assert a.loc["structural", "value_share"] == pytest.approx(0.8)
