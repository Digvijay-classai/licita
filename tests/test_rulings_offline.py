import json

import pytest

from licita.rulings import extract, offline

TEXT = 'Fundamento tercero. El PPT exige "bombas de infusión de la marca Acme modelo Z-200" sin admitir equivalentes.'
GROUND = {"ground": "brand_or_tailored_specs", "document": "PPT",
          "challenged_clause_quote": "bombas de infusión de la marca Acme modelo Z-200", "articles": ["art. 126 LCSP"],
          "holding": "upheld", "holding_summary_es": "Especificación de marca sin equivalentes."}
DATA = {"expediente": "A/1", "contract_object": "Lentes", "contracting_body": "Hospital X",
        "challenged_act": "pliegos", "outcome": "upheld", "grounds": [GROUND]}


@pytest.fixture
def con(tmp_path):
    c = extract._db(tmp_path / "r.duckdb")
    c.execute("insert into rulings (id, year, num, acto) values ('1/2024', 2024, 1, 'Pliegos'), "
              "('2/2024', 2024, 2, 'Pliegos')")
    c.execute("insert into ruling_texts (id, status, text) values ('1/2024', 'done', ?), ('2/2024', 'done', ?)",
              [TEXT, TEXT])
    return c


def _write(tmp_path, recs):
    p = tmp_path / "results.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs))
    return p


def test_import_verifies_quotes_and_keys_by_model(con, tmp_path):
    fake = {**GROUND, "challenged_clause_quote": "solvencia de cinco millones de euros anuales"}
    recs = [{"id": "1/2024", "model": "ollama/qwen3:14b", "status": "succeeded", "data": DATA,
             "input_tokens": 100, "output_tokens": 50, "seconds": 40.0},
            {"id": "2/2024", "model": "ollama/qwen3:14b", "status": "succeeded",
             "data": {**DATA, "grounds": [fake]}}]
    t = offline.import_results(_write(tmp_path, recs), con)
    assert t["succeeded"] == 2 and t["quotes"] == 2 and t["quotes_verified"] == 1
    rows = dict(con.execute("select id, quote_verified from ruling_grounds").fetchall())
    assert rows == {"1/2024": True, "2/2024": False}
    # a Mistral run on the same ruling sits alongside, not on top
    extract.store_result(con, "1/2024", "mistral-small-2603", "b1", DATA, 1, 1)
    assert con.execute("select count(*) from ruling_extractions where id = '1/2024'").fetchone()[0] == 2
    assert extract.pending(con, "all", "mistral-small-2603") == [("2/2024", TEXT)]


def test_malformed_output_is_stored_as_invalid_not_crashing(con, tmp_path):
    bad = {**DATA, "grounds": [{**GROUND, "ground": "made_up_ground"}]}
    recs = [{"id": "1/2024", "model": "m", "status": "succeeded", "data": bad},
            {"id": "2/2024", "model": "m", "status": "too_long", "error": "120000 chars"}]
    t = offline.import_results(_write(tmp_path, recs), con)
    assert t["invalid"] == 1 and t["failed"] == 1 and t["succeeded"] == 0
    st = dict(con.execute("select id, status from ruling_extractions").fetchall())
    assert st == {"1/2024": "invalid", "2/2024": "too_long"}


def test_validate():
    assert extract.validate(DATA) is None
    assert "required property" in extract.validate({"grounds": []})


def test_notebook_embeds_runner_and_smoke_test_default():
    nb = offline.notebook("print('runner')\n")
    src = ["".join(c["source"]) for c in nb["cells"]]
    assert "ollama.com/install.sh" in src[1]
    assert 'LICITA_LIMIT"] = "5"' in src[2]
    assert src[3].endswith("print('runner')\n")


def test_runner_reads_prompt_from_manifest_only():
    runner = offline.RUNNER.read_text()
    assert 'manifest["system"]' in runner and 'manifest["schema"]' in runner
    assert "untrusted" not in runner  # no prompt text duplicated in the runner


def test_repeated_grounds_are_collapsed_and_articles_merged(con):
    fanned = [GROUND, {**GROUND, "document": "PCAP"}, {**GROUND, "articles": ["art. 126 LCSP", "art. 1 LCSP"]}]
    n = extract.store_result(con, "1/2024", "m", "r", {**DATA, "grounds": fanned}, None, None)
    assert n["grounds"] == 1 and n["duplicates"] == 2
    (arts,) = con.execute("select articles from ruling_grounds").fetchone()
    assert json.loads(arts) == ["art. 126 LCSP", "art. 1 LCSP"]


def test_thinking_variant_is_tagged_separately():
    runner = offline.RUNNER.read_text()
    assert '("+think" if THINK else "")' in runner and 'r["model"] == TAG' in runner
