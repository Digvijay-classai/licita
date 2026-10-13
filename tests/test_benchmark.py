"""Benchmark metrics on a hand-made spine: timeliness, successor detection, at-risk value by tier,
competition gap with its interval and shrinkage, coverage, bands."""
from datetime import date

import duckdb
import pandas as pd
import pytest

from licita.spine import benchmark as bm
from licita.spine.build import VIEWS_SQL, brand_pattern, ext_months

AS_OF = date(2026, 10, 1)
NIF_A, NIF_B, NIF_C = "Q0000001A", "Q0000002B", "Q0000003C"


def _folder(nif, fid, title, cpv, start, months, procedure="1", ct="2", notice=None, pin=None, feed="643", budget=1000.0):
    return dict(entry_id=f"e-{nif}-{fid}", updated=f"{start}T10:00:00+02:00", link=None, title=title, folder_id=fid,
                status="RES", buyer=f"Hospital {nif}", buyer_nif=nif, buyer_dir3=None, buyer_city="madrid",
                buyer_parents="comunidad de ejemplo", buyer_type="1", is_health=True, contract_type=ct,
                contract_subtype=None, cpvs=cpv, budget_estimated=None, budget_total=None, budget_net=budget,
                nuts="ES300", planned_start=start, planned_end=None, duration=float(months), duration_unit="MON",
                extension_desc=None, extension_period=None, n_lots=0, lots_json=None, procedure=procedure,
                urgency=None, contracting_system=None, over_threshold=None, submission_deadline=None, funding=None,
                attachments_json="[]", notice_cn=notice, notice_pin=pin, notice_award=start, notice_formal=None,
                notices="", source_feed=feed, source="t", buyer_key_raw=nif, buyer_key=nif, n_snapshots=1)


def _result(nif, fid, start, bids=1.0, code="9", amount=900.0):
    return dict(entry_id=f"e-{nif}-{fid}", updated=f"{start}T10:00:00+02:00", folder_id=fid, buyer_nif=nif,
                source_feed="643", lot_id=None, result_code=code, result_desc=None, award_date=start, n_bids=bids,
                n_sme_bids=None, lower_bid=None, higher_bid=None, winner="W SA" if code != "3" else None,
                winner_nif="B1" if code != "3" else None, sme_awarded=None, award_net=amount if code != "3" else None,
                contract_id=None, contract_date=None, contract_start=None, buyer_key=nif, snapshot_status="RES",
                from_latest_snapshot=True)


@pytest.fixture
def con():
    folders, results = [], []
    # Buyer A: 30 recurring single-bid lots awarded 2022-2023 (12 months) -> ended 2023-2024; successors noticed
    # 120 days before the end for the first 20 (on time), after the end for 5, none for 5.
    cpvs = ["33150000", "33160000", "33170000", "33180000", "33190000", "33600000", "33610000", "33620000",
            "33630000", "33640000", "33650000", "33660000", "33670000", "33680000", "33690000", "33120000",
            "33130000", "33700000", "33710000", "33750000", "33760000", "33770000", "33790000", "33900000",
            "33910000", "33920000", "33930000", "33940000", "24110000", "24120000"]
    for i in range(30):
        start = date(2022, 1, 15) + pd.Timedelta(days=10 * i)
        fid = f"A{i:03d}"
        folders.append(_folder(NIF_A, fid, f"Suministro recurrente {i}", cpvs[i], start.isoformat(), 12))
        results.append(_result(NIF_A, fid, start.isoformat(), bids=1.0))
        end = start + pd.Timedelta(days=365)
        if i < 20:
            notice = end - pd.Timedelta(days=120)
        elif i < 25:
            notice = end + pd.Timedelta(days=30)
        else:
            continue
        sfid = f"A{i:03d}S"
        folders.append(_folder(NIF_A, sfid, f"Suministro recurrente {i} nuevo", cpvs[i], notice.isoformat(), 12,
                               notice=notice.isoformat()))
        results.append(_result(NIF_A, sfid, notice.isoformat(), bids=2.0))
    # 30 short single-bid lots in the cell shared with buyer B (not recurring: 6 months)
    for i in range(30):
        start = (date(2023, 2, 1) + pd.Timedelta(days=20 * i)).isoformat()
        folders.append(_folder(NIF_A, f"A-O{i:03d}", f"Compra puntual {i}", "33140000", start, 6))
        results.append(_result(NIF_A, f"A-O{i:03d}", start, bids=1.0))
    # one past-term tier-1 contract (dialysis consumables) with no successor, €12,000/yr, ended 100 days ago
    s = (AS_OF - pd.Timedelta(days=465)).isoformat()
    folders.append(_folder(NIF_A, "A-DIAL", "Fungible de diálisis", "33181500", s, 12, budget=12000.0))
    results.append(_result(NIF_A, "A-DIAL", s, amount=12000.0))
    # one deferrable (furniture) contract due in 100 days with no successor, €10,000/yr
    s = (AS_OF - pd.Timedelta(days=265)).isoformat()
    folders.append(_folder(NIF_A, "A-FURN", "Mobiliario", "39100000", s, 12, budget=10000.0))
    results.append(_result(NIF_A, "A-FURN", s, amount=10000.0))
    # a deserted lot and an extension published as an award
    folders.append(_folder(NIF_A, "A-DES", "Suministro desierto", "33140000", "2024-03-01", 12))
    results.append(_result(NIF_A, "A-DES", "2024-03-01", bids=0.0, code="3"))
    folders.append(_folder(NIF_A, "A-PRO", "Prórroga del contrato de limpieza", "90910000", "2024-05-01", 12))
    results.append(_result(NIF_A, "A-PRO", "2024-05-01", bids=1.0))
    # Buyer B: 40 open lots with two bids, same cell -> below expected; works contracts excluded from recurring
    for i in range(40):
        start = (date(2023, 1, 10) + pd.Timedelta(days=15 * i)).isoformat()
        folders.append(_folder(NIF_B, f"B{i:03d}", f"Servicio {i}", "33140000", start, 24))
        results.append(_result(NIF_B, f"B{i:03d}", start, bids=2.0))
    folders.append(_folder(NIF_B, "B-WORK", "Obras de reforma", "45210000", "2023-06-01", 18, ct="3"))
    results.append(_result(NIF_B, "B-WORK", "2023-06-01", bids=3.0))
    # Buyer C: too few lots -> not in the population
    folders.append(_folder(NIF_C, "C1", "Algo", "33140000", "2024-01-01", 12))
    results.append(_result(NIF_C, "C1", "2024-01-01"))

    def typed(rows):
        df = pd.DataFrame(rows)
        for c in df.columns:                     # all-None columns must register as text, not integer
            if df[c].isna().all():
                df[c] = df[c].astype("string")
        return df

    db = duckdb.connect()
    db.create_function("ext_months", ext_months, ["VARCHAR"], "DOUBLE", null_handling="special")
    db.register("folders_df", typed(folders))
    db.register("results_df", typed(results))
    db.execute("create table folders as select * from folders_df")
    db.execute("create table results as select * from results_df")
    db.execute("create table brand_regex as select ? as pattern", [brand_pattern()])
    db.execute(VIEWS_SQL.read_text())
    db.execute("create table lots as select * from v_contract_terms")
    db.execute("create table peers as select * from v_peers")
    return db


def test_population_and_identity(con):
    df = bm.build_benchmark(con, AS_OF)
    assert set(df.buyer_key) == {NIF_A, NIF_B}            # C has one lot
    a = df[df.buyer_key == NIF_A].iloc[0]
    assert a.region == "ES30" and a.feed_mix == 0 and a.n_lots >= 30
    assert a.coverage_score >= 0.8 and bool(a.comparable)
    assert bool(a.hospital_like) and df.hospital_like.all()        # names are "Hospital <nif>"


def test_hospital_like_regex_is_generic():
    assert bm.HOSPITAL_RE.search("hospital comarcal de x")
    assert bm.HOSPITAL_RE.search(bm.normalise_city("Servicio de Salud de Ejemplo"))
    assert not bm.HOSPITAL_RE.search("concejalia de salud publica del ayuntamiento")
    assert not bm.HOSPITAL_RE.search("delegacion especial de la agencia tributaria")


def test_timeliness_shares(con):
    a = bm.build_benchmark(con, AS_OF).set_index("buyer_key").loc[NIF_A]
    # 30 originals ended 2023-24 + 25 successors ended later + dialysis; the shares are over all ended ones
    assert a.n_ended >= 30
    assert 0 < a.on_time_share < 1 and 0 < a.late_share < a.on_time_share
    assert a.no_successor_share > 0


def test_successor_detection_is_same_buyer_same_cpv(con):
    bm._prepare(con, date(2022, 1, 1))
    s = con.execute("select folder_id, successor_seen from bm_successors where buyer_key = ? and folder_id in ('A000','A022','A027')",
                    [NIF_A]).fetchall()
    d = dict(s)
    assert d["A000"] is not None and d["A022"] is not None and d["A027"] is None


def test_at_risk_value_is_tier_weighted(con):
    a = bm.build_benchmark(con, AS_OF).set_index("buyer_key").loc[NIF_A]
    assert a.past_term_no_successor_eur_yr == pytest.approx(12000.0)
    assert a.value_at_risk_eur_yr == pytest.approx(12000.0 * 1.0 + 10000.0 * 0.2)
    assert a.tier1_share == pytest.approx(12000 / 22000)


def test_deserted_and_extension_shares(con):
    a = bm.build_benchmark(con, AS_OF).set_index("buyer_key").loc[NIF_A]
    assert a.deserted_rate > 0 and a.extension_award_share > 0
    b = bm.build_benchmark(con, AS_OF).set_index("buyer_key").loc[NIF_B]
    assert b.deserted_rate == 0


def test_competition_gap_interval_and_shrinkage(con):
    df = bm.build_benchmark(con, AS_OF).set_index("buyer_key")
    a, b = df.loc[NIF_A], df.loc[NIF_B]
    assert a.single_bid_rate > b.single_bid_rate
    assert a.gap > 0 > b.gap
    assert a.gap_lo <= a.gap <= a.gap_hi and b.gap_lo <= b.gap <= b.gap_hi
    assert abs(a.shrunk_gap) < abs(a.gap) or abs(b.shrunk_gap) < abs(b.gap)   # pulled toward the regional mean
    assert a.band_competition in ("on_track", "watch", "behind") and b.band_competition in ("on_track", "watch", "behind")


def test_wilson_known_values():
    lo, hi = bm.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)
    assert bm.wilson(0, 0) == (0.0, 1.0)
    lo, hi = bm.wilson(0, 20)
    assert lo == 0.0 and hi < 0.2


def test_shrinkage_moves_small_samples_more():
    assert bm.shrink(0.3, 10, 0.0) < bm.shrink(0.3, 500, 0.0) < 0.3
    assert bm.shrink(0.3, 50, 0.1) == pytest.approx(0.2)


def test_tier_lookup_longest_prefix_and_default():
    assert bm.tier_for("33181500") == (1, "high")          # dialysis consumables
    assert bm.tier_for("33192000")[0] == 2                 # medical furniture: group rule
    assert bm.tier_for("39100000")[0] == 3
    assert bm.tier_for("33111000")[0] == 3 and bm.tier_for("50421000")[0] == 1   # purchase vs maintenance
    assert bm.tier_for("99999999") == (3, "low") and bm.tier_for(None) == (3, "low")


def test_tiers_csv_is_well_formed():
    t = bm.load_tiers()
    assert set(t.tier.unique()) <= {1, 2, 3} and set(t.confidence.unique()) <= {"high", "medium"}
    assert t.cpv_prefix.str.fullmatch(r"[0-9]{2,8}").all()
    assert len(t) >= 150


def test_bands_tertiles():
    s = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    b = bm.bands(s, higher_is_better=True)
    assert b.iloc[0] == "behind" and b.iloc[-1] == "on_track" and "watch" in set(b)
    assert bm.bands(pd.Series([0.1, 0.2]), True).tolist() == ["watch", "watch"]
