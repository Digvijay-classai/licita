import duckdb
import pytest

from licita.rulings import db


def _v1(path):
    """A database as the pre-Mistral code wrote it."""
    c = duckdb.connect(str(path))
    c.execute("""create table rulings (id varchar primary key, year int, num int, url varchar, status varchar,
                 error varchar, fecha varchar, organo varchar, acto varchar, objeto varchar, tipo_contrato varchar,
                 tipo_resolucion varchar, summary varchar, pdf_url varchar, fetched_at timestamp)""")
    c.execute("""create table ruling_texts (id varchar primary key, status varchar, error varchar, pages int,
                 chars int, text varchar, fetched_at timestamp)""")
    c.execute("""create table ruling_extractions (id varchar, model varchar, run_id varchar, status varchar,
                 error varchar, data json, input_tokens int, output_tokens int, collected_at timestamp,
                 primary key (id, model))""")
    c.execute("""create table extraction_batches (batch_id varchar primary key, model varchar, n int, scope varchar,
                 submitted_at timestamp, collected_at timestamp)""")
    c.execute("insert into rulings (id, year, num, acto) values ('1/2030', 2030, 1, 'Pliegos')")
    c.execute("insert into ruling_texts (id, status, text) values ('1/2030', 'done', 'texto')")
    c.execute("insert into ruling_extractions (id, model, status) values ('1/2030', 'ollama/qwen3:14b', 'done')")
    c.close()


def test_migration_qualifies_ids_everywhere_and_keeps_rows(tmp_path):
    path = tmp_path / "r.duckdb"
    _v1(path)
    con = db.connect(path, source="tre")
    assert con.execute("select id, source, num from rulings").fetchall() == [("tre:1/2030", "tre", 1)]
    assert con.execute("select id, text_source from ruling_texts").fetchall() == [("tre:1/2030", "pdfplumber")]
    assert con.execute("select id, model, provider from ruling_extractions").fetchall() == [
        ("tre:1/2030", "ollama/qwen3:14b", None)]
    assert con.execute("select value from schema_meta where key = 'version'").fetchone() == ("2",)
    con.close()
    again = db.connect(path, source="other")             # idempotent: never re-prefixes
    assert again.execute("select id from rulings").fetchall() == [("tre:1/2030",)]
    with pytest.raises(duckdb.ConstraintException):       # primary keys survive the rebuild
        again.execute("insert into rulings (id) values ('tre:1/2030')")


def test_fresh_database_starts_at_current_version(tmp_path):
    con = db.connect(tmp_path / "new.duckdb")
    assert con.execute("select value from schema_meta").fetchone() == (str(db.SCHEMA_VERSION),)


def test_ids_round_trip():
    assert db.ruling_id(7, 2025) == "tre:7/2025"         # test profile tribunal short name "TRE"
    assert db.split_id("tre:7/2025") == ("tre", 7, 2025)
    assert db.split_id("7/2025") == (None, 7, 2025)
    with pytest.raises(ValueError):
        db.split_id("tre_7_2025")


def test_ocr_fills_scanned_rulings_and_records_the_source(tmp_path, monkeypatch):
    from licita.rulings import pdfs
    monkeypatch.setattr(pdfs, "PDF_DIR", tmp_path)
    (tmp_path / "tre_2024_1.pdf").write_bytes(b"%PDF scan")
    con = db.connect(tmp_path / "r.duckdb")
    con.execute("insert into ruling_texts (id, status, text) values ('tre:1/2024', 'no_text', ''), "
                "('tre:2/2024', 'no_text', '')")

    class FakeOCR:
        def model_for(self, task):
            return "mistral-ocr-latest"

        def resolve_model(self, m):
            return "mistral-ocr-2512"

        def ocr(self, data, model):
            assert data == b"%PDF scan"
            return "x" * 600, 3

    t = pdfs.ocr_missing(con, provider=FakeOCR())
    assert t == {"ocr_done": 1, "ocr_failed": 0, "no_pdf": 1}
    assert con.execute("select status, text_source, pages from ruling_texts where id = 'tre:1/2024'").fetchone() == (
        "done", "mistral-ocr:mistral-ocr-2512", 3)


def test_label_sample_is_stratified_and_skips_non_pliegos(tmp_path):
    from licita.evals.label import sample
    con = db.connect(tmp_path / "r.duckdb")
    for i, (acto, tipo) in enumerate([("Pliegos", "Estimación")] * 5 + [("Pliegos", "Desestimación")] * 5
                                     + [("Adjudicación", "Estimación")] * 5, 1):
        con.execute("insert into rulings (id, acto, tipo_resolucion, year, num) values (?, ?, ?, 2024, ?)",
                    [f"tre:{i}/2024", acto, tipo, i])
        con.execute("insert into ruling_texts (id, status, chars) values (?, 'done', 1000)", [f"tre:{i}/2024"])
    rows = sample(con, 4)
    assert len(rows) == 4 and {r["tribunal_label"] for r in rows} == {"Estimación", "Desestimación"}
    assert all(r["outcome"] is None for r in rows)


def test_failed_migration_rolls_back_to_v1(tmp_path, monkeypatch):
    path = tmp_path / "r.duckdb"
    _v1(path)
    real = db._rebuild
    calls = []

    def flaky(con, table, select_sql):
        calls.append(table)
        if len(calls) == 2:                               # crash after the first table was rebuilt
            raise RuntimeError("disk full")
        real(con, table, select_sql)

    monkeypatch.setattr(db, "_rebuild", flaky)
    with pytest.raises(RuntimeError):
        db.connect(path, source="tre")
    c = duckdb.connect(str(path))
    assert c.execute("select id from rulings").fetchall() == [("1/2030",)]
    assert c.execute("select id from ruling_texts").fetchall() == [("1/2030",)]
    assert c.execute("select count(*) from schema_meta where key = 'version'").fetchone() == (0,)
    c.close()
    monkeypatch.setattr(db, "_rebuild", real)
    assert db.connect(path, source="tre").execute("select id from rulings").fetchall() == [("tre:1/2030",)]


def test_scrape_refuses_a_database_from_another_tribunal(tmp_path):
    from licita.errors import LicitaError
    from licita.rulings import scrape
    con = db.connect(tmp_path / "r.duckdb")
    assert scrape.check_source(con) == "tre"                       # empty database: any profile
    con.execute("insert into rulings (id, source) values ('other:1/2024', 'other')")
    with pytest.raises(LicitaError, match="does not match"):
        scrape.check_source(con)
