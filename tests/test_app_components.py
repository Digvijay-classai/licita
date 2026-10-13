from datetime import date

import pandas as pd

from licita.app import components as ui


def _sheet(bids=1.0, peer_single=0.3, framework=False, eur=20_000.0):
    return {"contract": {"median_bids": bids, "framework_derived": framework, "title": "<b>X</b>"},
            "peer_benchmark": {"topic": {"single_bid_rate": peer_single, "median_bids": 3.0, "n": 40,
                                         "cpv": "50", "terms": ["resonancia"]}, "code": None},
            "bidder_value": {"eur_per_year": eur}}


def test_headlines_follow_the_evidence():
    assert "acuerdo marco" in ui.headline(_sheet(framework=True), "es")
    assert "exclusividad" in ui.headline(_sheet(peer_single=0.92), "es")
    assert "30 %" in ui.headline(_sheet(peer_single=0.3), "es")
    assert "3" in ui.headline(_sheet(bids=2.0), "en")


def test_exclusive_contract_never_shows_plus_one_figure_on_radar():
    df = pd.DataFrame([{"folder_id": "F1", "title": "MRI", "flag": "start_now", "start_by": None,
                        "end_initial": None, "min_bids": 1.0, "annual_budget": 100_000.0}])
    html = ui.radar_table(df, {"F1": _sheet(peer_single=0.92)}, date(2026, 10, 8), "es")
    assert "20.000 €" not in html
    html = ui.radar_table(df, {"F1": _sheet(peer_single=0.3)}, date(2026, 10, 8), "es")
    assert "20.000 €" in html


def test_data_text_is_escaped():
    df = pd.DataFrame([{"folder_id": "<script>", "title": "<img src=x onerror=alert(1)>", "flag": "verify",
                        "start_by": None, "end_initial": None, "min_bids": None, "annual_budget": None}])
    html = ui.radar_table(df, {}, date(2026, 10, 8), "en")
    assert "<img" not in html and "<script>" not in html and "&lt;img" in html


def test_framework_contract_has_no_empty_chair():
    assert "lk-empty" not in ui.chairs(3, None, "es", expect=False)
    assert ui.chairs(3, None, "es").count("lk-dot") == 4


def test_scope_labels_are_localised():
    assert ui.scope_label({"cpv": "5000", "band": "b<100k"}, "es") == "CPV 5000, lotes de 15.000 a 100.000 €"
    assert "title with" in ui.scope_label({"cpv": "50", "terms": ["resonancia"]}, "en")


def test_continuity_headline_rules():
    from datetime import date

    from licita.app import components as ui
    as_of = date(2026, 10, 10)
    late = {"flag": "start_now", "start_by": "2026-04-26", "end_initial": "2026-11-26", "title": "x"}
    assert ui.continuity_headline(late, as_of, "en") == "The file should have started 167 days ago"
    due = {"flag": "due_6m", "start_by": "2026-12-03", "end_initial": "2027-10-31", "title": "x"}
    assert ui.continuity_headline(due, as_of, "es") == "Quedan 54 días para iniciar el expediente"
    ver = {"flag": "verify", "start_by": None, "end_initial": "2026-07-01", "title": "x"}
    assert "01/07/2026" in ui.continuity_headline(ver, as_of, "es")
    truth = {**late, "truth": {"status": "extended", "source": "team", "recorded_on": "2026-10-20"}}
    assert ui.continuity_headline(truth, as_of, "en").startswith("Confirmed by the team (team, 2026-10-20)")
    assert ui.continuity_headline({"flag": "ok", "title": "fallback"}, as_of, "en") == "fallback"


def test_continuity_key_orders_late_files_first_and_frameworks_last():
    from licita.app import components as ui

    def s(flag, days, fw=False, annual=1.0):
        return {"contract": {"flag": flag, "days_past_start_by": days, "framework_derived": fw, "annual_budget": annual}}
    sheets = [s("due_6m", -30), s("start_now", 10, fw=True), s("verify", None), s("start_now", 120), s("start_now", 40)]
    ordered = sorted(sheets, key=ui.continuity_key)
    assert [(x["contract"]["flag"], x["contract"]["days_past_start_by"]) for x in ordered] == [
        ("start_now", 120), ("start_now", 40), ("verify", None), ("due_6m", -30), ("start_now", 10)]


def test_reason_line_escapes_and_handles_missing():
    from licita.app import components as ui
    assert ui.reason_line({"reason": None}, "es") is None
    line = ui.reason_line({"reason": {"bucket": "addressable", "confidence": 0.7, "lever": "<consulta>"}}, "es")
    assert "Abordable" in line and "&lt;consulta&gt;" in line and "70" in line
