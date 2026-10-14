from pathlib import Path

import pytest

from licita.evals import metrics as m
from licita.evals import run

FIX = Path(__file__).parent / "fixtures" / "evals"


def test_wilson_interval_known_values():
    lo, hi = m.wilson(8, 10)
    assert round(lo, 3) == 0.490 and round(hi, 3) == 0.943
    assert m.wilson(0, 0) is None and m.wilson(0, 5)[0] == 0.0 and m.wilson(5, 5)[1] == 1.0


def test_rate_formatting():
    assert m.Rate(0, 0).fmt() == "–"
    assert m.Rate(3, 4).fmt().startswith("75% [")


def test_ground_counts_are_multisets():
    gold = [{"ground": "a", "holding": "upheld"}, {"ground": "a", "holding": "dismissed"}, {"ground": "b", "holding": "upheld"}]
    pred = [{"ground": "a", "holding": "upheld"}, {"ground": "c", "holding": "upheld"}]
    assert m.ground_counts(gold, pred) == (1, 2, 3)
    assert m.holding_matches(gold, pred) == (0, 1)       # sorted pairing: dismissed vs upheld


def test_kappa():
    assert m.cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert m.cohen_kappa([], []) is None
    assert m.cohen_kappa(["a", "a"], ["a", "a"]) == 1.0


def test_percentile():
    assert m.percentile([1, 2, 3, 4], 0.5) == 2.5 and m.percentile([], 0.5) is None


def test_missing_or_empty_golden_set_refuses_to_report(tmp_path):
    with pytest.raises(run.EvalError, match="no golden set"):
        run.load_golden(tmp_path / "nope.jsonl")
    (tmp_path / "g.jsonl").write_text("\n")
    with pytest.raises(run.EvalError, match="empty"):
        run.load_golden(tmp_path / "g.jsonl")
    (tmp_path / "g.jsonl").write_text('{"id": "x"}\n')
    with pytest.raises(run.EvalError, match="without labels"):
        run.load_golden(tmp_path / "g.jsonl")


def test_replay_scores_every_model_and_flags_synthetic_data():
    text = run.replay(FIX)
    assert text.startswith("> **SYNTHETIC FIXTURES")
    assert "| synthetic-model-a |" in text and "| synthetic-model-b |" in text
    assert "'length': 1" in text and "Wilson 95%" in text and "confirmed against" in text
    assert "Double-labelled rulings: 4" in text


def test_small_samples_hide_precision_and_recall(tmp_path):
    import shutil
    for f in ("rulings.jsonl", "responses.jsonl"):
        shutil.copy(FIX / f, tmp_path / f)
    (tmp_path / "golden.jsonl").write_text("\n".join((FIX / "golden.jsonl").read_text().splitlines()[:3]) + "\n")
    text = run.replay(tmp_path)
    assert f"n<{m.MIN_N_FOR_PR}" in text


def test_score_without_any_extractions_explains_how_to_run(tmp_path):
    from licita.rulings import extract
    con = extract._db(tmp_path / "r.duckdb")
    with pytest.raises(run.EvalError, match="no model has extractions"):
        run.report(con, run.load_golden(FIX / "golden.jsonl"))


def test_tribunal_reference_scores_outcome_and_withholds_ground_metrics(tmp_path):
    import json
    import shutil
    for f in ("rulings.jsonl", "responses.jsonl"):
        shutil.copy(FIX / f, tmp_path / f)
    sample = tmp_path / "to_label.jsonl"
    sample.write_text("".join(json.dumps({"id": r["id"], "tribunal_label": r.get("tipo_resolucion")}) + "\n"
                              for r in map(json.loads, (FIX / "rulings.jsonl").read_text().splitlines())))
    gold = run.tribunal_golden(sample)
    assert gold and all(g["grounds"] is None and g["outcome"] != "unknown" for g in gold)
    (tmp_path / "golden.jsonl").write_text("".join(json.dumps(g) + "\n" for g in gold))
    text = run.replay(tmp_path)
    assert "Reference: the tribunal's published outcome" in text
    assert text.count(run.PENDING) >= 3 * 2      # three ground columns per model row
    with pytest.raises(run.EvalError, match="make label"):
        run.tribunal_golden(tmp_path / "missing.jsonl")
