import json

import pytest

from licita.evals import label
from licita.rulings import extract

TEXT = ("SYNTHETIC FIXTURE. Resolución ficticia.\nFUNDAMENTOS DE DERECHO\nPRIMERO. El pliego exige solvencia.\n"
        "Por todo lo anterior, este Tribunal ACUERDA:\nPrimero. Estimar parcialmente el recurso.\n")


def _db(tmp_path):
    con = extract._db(tmp_path / "r.duckdb")
    for rid in ("synth:1/2025", "synth:2/2025"):
        con.execute("insert into rulings (id, acto, tipo_resolucion, year, num) values (?, 'Pliegos', 'Estimación', 0, 0)",
                    [rid])
        con.execute("insert into ruling_texts (id, status, text) values (?, 'done', ?)", [rid, TEXT])
    return con


def test_operative_part_starts_at_the_last_acuerda_heading():
    assert label.operative_part(TEXT).startswith("Por todo lo anterior, este Tribunal ACUERDA")
    assert label.operative_part("sin encabezado") is None


def test_kit_has_sheets_and_answer_template_but_no_tribunal_label(tmp_path):
    rows = [{"id": "synth:1/2025", "tribunal_label": "Estimación"}, {"id": "synth:2/2025", "tribunal_label": "x"}]
    out = label.write_kit(_db(tmp_path), rows, tmp_path / "kit")
    sheet = (out / "01.txt").read_text()
    assert "PARTE DISPOSITIVA" in sheet and "Estimar parcialmente" in sheet and "Estimación" not in sheet
    head = (out / "respuestas.csv").read_text(encoding="utf-8-sig").splitlines()
    assert head[0] == ";".join(label.COLUMNS) and head[1].startswith("01;synth:1/2025")


def _answers(tmp_path, lines, sep=";"):
    p = tmp_path / "a.csv"
    p.write_text("﻿" + sep.join(label.COLUMNS) + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_import_groups_grounds_maps_spanish_values_and_skips_blank_rows(tmp_path):
    p = _answers(tmp_path, ["01;synth:1/2025;Estimado parcial;1;disproportionate_solvency;estimado;dudoso",
                            "01;synth:1/2025;estimado_parcial;2;lots_division;Desestimado;",
                            "02;synth:2/2025;inadmitido;;;;",
                            "03;synth:3/2025;;;;;"])
    got = {g["id"]: g for g in label.read_answers(p, "A")}
    assert set(got) == {"synth:1/2025", "synth:2/2025"}
    assert got["synth:1/2025"]["outcome"] == "partly_upheld" and got["synth:1/2025"]["notes"] == "dudoso"
    assert got["synth:1/2025"]["grounds"] == [{"ground": "disproportionate_solvency", "holding": "upheld"},
                                              {"ground": "lots_division", "holding": "dismissed"}]
    assert got["synth:2/2025"] == {"id": "synth:2/2025", "outcome": "inadmissible", "grounds": [], "labeller": "A",
                                   "notes": ""}


def test_import_reports_every_bad_row_and_writes_nothing(tmp_path):
    p = _answers(tmp_path, ["01;synth:1/2025;ganado;1;lots_division;estimado;",
                            "02;synth:2/2025;desestimado;1;marca;quizas;"])
    with pytest.raises(label.LabelError) as e:
        label.read_answers(p, "A")
    assert "row 2" in e.value.cause and "row 3: categoria" in e.value.cause and "row 3: decision" in e.value.cause


def test_merge_second_labeller_attaches_for_agreement(tmp_path):
    golden = tmp_path / "g.jsonl"
    a = [{"id": "x:1/2025", "outcome": "upheld", "grounds": [], "labeller": "A", "notes": ""}]
    b = [{"id": "x:1/2025", "outcome": "dismissed", "grounds": [], "labeller": "B", "notes": ""}]
    assert label.merge_golden(a, golden) == (1, 0)
    assert label.merge_golden(b, golden) == (0, 1)
    assert label.merge_golden(a, golden) == (0, 0)          # re-importing A keeps B's second label
    row = json.loads(golden.read_text())
    assert row["second"]["outcome"] == "dismissed" and row["second"]["labeller"] == "B"
    with pytest.raises(label.LabelError, match="labeller A has not"):
        label.merge_golden([{**b[0], "id": "x:2/2025"}], golden)
