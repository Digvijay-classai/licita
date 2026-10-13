from datetime import date

import duckdb
import pytest

from licita.memo import facts


def test_framework_and_extension_detection():
    assert facts.is_framework_derived("Suministro de apósitos derivado del Acuerdo Marco 14/2022")
    assert facts.is_framework_derived("Factor VIII ... (Derivado AM Ejemplo 1/2030)")
    assert facts.is_framework_derived("Vigilancia, Basado Acuerdo Marco de Homologación")
    assert not facts.is_framework_derived("Mantenimiento de la resonancia magnética Philips")
    assert facts.is_extension("PRORROGA del expediente PNSP 0042")
    assert facts.is_extension("Prórroga del expediente EXP-EJ-42")


def test_manufacturer_match_is_whole_word():
    assert facts.incumbent_is_manufacturer("Mantenimiento de los respiradores Dräger", "Dräger Hispania, S.A.U")
    assert facts.incumbent_is_manufacturer("camas especiales marca HILL-ROM", "HILLROM IBERIA SLU")
    # 'ORAS' is inside 'incubadoras' once spaces are removed: must not match
    assert not facts.incumbent_is_manufacturer("mantenimiento de las incubadoras", "ORAS")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("""create table lots (buyer_nif varchar, buyer_key varchar, title varchar, cpv2 varchar, cpv4 varchar,
                 is_open boolean, is_health boolean, n_bids double, discount double, award_d date,
                 winner varchar, winner_nif varchar, nuts varchar)""")
    rows = []
    for i in range(12):   # MRI maintenance elsewhere, filed under the generic 5000 code
        rows.append(("B1", f"B{i}", "Mantenimiento resonancia magnética", "50", "5000", True, True, 1 + i % 2,
                     0.1, date(2024, 1, 1), "IMAGING SERVICE SL" if i % 2 else "OTHER SL",
                     "ES-B11111111" if i % 2 else "B22222222", "ES99"))
    for i in range(40):   # facility maintenance under the same generic code
        rows.append(("B2", f"C{i}", "Mantenimiento de ascensores", "50", "5000", True, True, 3, 0.2,
                     date(2024, 1, 1), "LIFTS SA", "A33333333", "ES30"))
    rows.append(("H", "H", "Mantenimiento resonancia magnética GE", "50", "5000", True, True, 1, 0.0,
                 date(2023, 1, 1), "OTHER SL", "B22222222", "ES99"))
    rows.append(("B3", "B3", "Mantenimiento resonancia magnética", "50", "5000", True, True, 1, 0.0,
                 date(2024, 1, 1), "PEREZ GARCIA JUAN", "12345678Z", "ES99"))
    rows.append(("B4", "B4", "Mantenimiento resonancia magnética", "50", "5000", True, True, 1, 0.0,
                 date(2024, 2, 1), "PEREZ GARCIA JUAN", "12345678Z", "ES99"))
    c.executemany("insert into lots values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    c.execute("create table peers as select null::varchar cpv4, null::varchar band, 0 n, 0.0 single_bid_rate, "
              "0.0 median_bids, 0.0 median_discount where false")
    return c


def test_topic_peers_separate_mri_from_lifts(con, monkeypatch):
    monkeypatch.setattr(facts, "TOPIC_MIN_DF", 5)
    monkeypatch.setattr(facts, "TOPIC_MAX_SHARE", 0.5)
    terms = facts.topic_terms(con, "Mantenimiento integral resonancia magnética modelo RM-1200 GE", "GE HEALTHCARE")
    assert set(terms) == {"resonancia", "magnetica"}
    p = facts.peer_benchmark(con, "5000", "b<100k", "50", terms)
    assert p["code"]["n"] == 55                     # code peers mix MRI and lifts
    assert p["topic"]["n"] == 15 and p["topic"]["small_sample"]


def test_invite_list_excludes_incumbents_and_individuals(con):
    inv = facts.invite_list(con, "H", "5000", date(2026, 10, 1), cpv2="50", terms=["resonancia"])
    nifs = {i["nif"] for i in inv}
    assert "B11111111" in nifs                      # 'ES-' prefix normalised away
    assert "B22222222" not in nifs                  # already won this topic at the hospital
    assert "12345678Z" not in nifs                  # natural person: never listed
    assert "A33333333" not in nifs                  # lifts company: off-topic


def test_bidder_value_reports_insignificant_steps_without_euros():
    c = duckdb.connect()
    c.execute("create table bidder_value (cpv2 varchar, k bigint, n_k bigint, n_k1 bigint, median_k double, "
              "median_k1 double, delta double, ci_lo double, ci_hi double)")
    c.execute("insert into bidder_value values ('50', 1, 100, 50, 0.0, 0.15, 0.15, 0.12, 0.18), "
              "('50', 3, 100, 50, 0.2, 0.21, 0.01, -0.01, 0.03)")
    b = facts.bidder_value(c, "50", 1.0, 100_000)
    assert b["significant"] and b["eur_per_year"] == 15_000 and b["eur_per_year_ci90"] == [12_000, 18_000]
    b3 = facts.bidder_value(c, "50", 3.0, 100_000)
    assert not b3["significant"] and b3["eur_per_year"] is None
    assert facts.bidder_value(c, "50", 8.0, 100_000) is None   # beyond the curve
