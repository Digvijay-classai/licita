from datetime import date

from licita.spine.successors import Candidate, Contract, grams, jaccard, match, normalise


def C(title, cpv="5042", award=date(2023, 1, 1), end=date(2024, 1, 1), lots=0):
    return Contract("Q1", "OLD", title, frozenset({cpv}), award, end, end, lots)


def K(title, fid="NEW", cpv="5042", seen=date(2023, 10, 1), status="ADJ", proc="1", buyer="Hosp", lots=0):
    return Candidate("Q1", buyer, fid, title, frozenset({cpv}), seen, status, proc, lots)


def test_boilerplate_removed():
    assert normalise("Suministro de gasas con destino al Hospital Universitario Santa Ficticia") == "gasas"
    # boilerplate-only titles must not look similar to each other
    a = grams(normalise("Servicio de limpieza del Hospital"))
    b = grams(normalise("Servicio de lavandería del Hospital"))
    assert jaccard(a, b) < 0.4


def test_replaced():
    m = match(C("Mantenimiento integral del equipo de rayos X telemando"),
              [K("Mantenimiento integral del equipo de rayos X con telemando")])
    assert m.status == "replaced" and m.confidence >= 0.5


def test_prorroga_is_extended():
    m = match(C("Servicio de lavado de ropa"), [K("Prórroga referente al servicio de lavado de ropa")])
    assert m.status == "extended"


def test_framework_absorption_and_central_buyer():
    assert match(C("Suministro de gases medicinales"),
                 [K("Gases medicinales", proc="7")]).status == "absorbed_by_framework"
    assert match(C("Suministro de gases medicinales"),
                 [K("Gases medicinales", buyer="Central de Compras de Ejemplo")]
                 ).status == "absorbed_by_framework"


def test_in_progress_and_partial():
    assert match(C("Suministro de prótesis de cadera"), [K("Prótesis de cadera", status="PUB")]).status == "in_progress"
    assert match(C("Suministro de prótesis de cadera", lots=1),
                 [K("Prótesis de cadera", lots=3)]).status == "replaced_partial"


def test_no_successor_when_nothing_close():
    m = match(C("Mantenimiento de autoclaves de esterilización"), [K("Suministro de café para la cafetería", cpv="1586")])
    assert m.status == "no_successor" and m.confidence >= 0.9


def test_candidate_outside_time_window_ignored():
    m = match(C("Mantenimiento de autoclaves de esterilización"),
              [K("Mantenimiento de autoclaves de esterilización", seen=date(2019, 1, 1))])   # before award
    assert m.status == "no_successor"


def test_weak_match_needs_verification():
    m = match(C("Mantenimiento de equipos de climatización de quirófano"),
              [K("Mantenimiento de equipos de cocina", cpv="5088")])
    assert m.status in ("verify", "no_successor")
    assert m.status != "replaced"


def test_late_successor_found_only_in_open_ended_pass():
    from datetime import date

    from licita.spine.successors import Candidate, Contract, match
    c = Contract("A", "F1", "Mantenimiento de los ascensores y montacargas del edificio principal",
                 frozenset({"5000"}), date(2019, 2, 1), date(2020, 2, 1), date(2020, 2, 1), 0)
    late = Candidate("A", "Hospital", "F9",
                     "Mantenimiento de ascensores y montacargas del edificio principal",
                     frozenset({"5000"}), date(2024, 9, 1), "ADJ", "1")
    assert match(c, [late]).status == "no_successor"
    m = match(c, [late], open_ended=True)
    assert m.status == "replaced_late" and m.successor_folder == "F9"


def test_radar_candidates_include_the_profiles_central_buyer_only():
    import duckdb

    from licita.spine.radar import candidates_for
    con = duckdb.connect()
    con.execute("""create table folders (buyer_key varchar, buyer_nif varchar, buyer varchar, folder_id varchar,
                   title varchar, cpvs varchar, status varchar, procedure varchar, n_lots int)""")
    con.execute("create table snapshots (buyer_key varchar, folder_id varchar, notice_pin varchar, "
                "notice_cn varchar, updated varchar)")
    for key, nif, buyer in [("a", "Q0000001A", "Hospital A"), ("c", None, "Central de Compras de Ejemplo"),
                            ("o", None, "Agencia de Compras de Otra Region")]:
        con.execute("insert into folders values (?, ?, ?, ?, 'x', '33100000', 'ADJ', '1', 1)", [key, nif, buyer, key])
        con.execute("insert into snapshots values (?, ?, '2024-01-01', null, null)", [key, key])
    assert {c.folder_id for c in candidates_for(con, "Q0000001A")} == {"a", "c"}
