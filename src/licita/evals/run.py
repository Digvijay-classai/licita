"""Score ruling-extraction models against the golden set; run live models; replay cached outputs offline.

  python -m licita.evals.run score  [--golden FILE] [--models a,b]     score what is already in rulings.duckdb
  python -m licita.evals.run live   --models mistral-small-latest,mistral-medium-latest [--golden FILE]
                                                                        call each model on golden rulings, then score
  python -m licita.evals.run replay FIXTURE_DIR                          offline: cached responses -> temp db -> score

Golden set: JSONL, one ruling per line (default `private/golden/rulings.jsonl`, or LICITA_GOLDEN). It stays
private because real rulings name contracting bodies. Line format:
  {"id": "<source>:<num>/<year>", "outcome": "upheld|partly_upheld|dismissed|inadmissible|other",
   "grounds": [{"ground": "<category>", "holding": "upheld|dismissed|not_decided"}],
   "labeller": "A", "second": {"outcome": ..., "grounds": [...], "labeller": "B"}}      ("second" is optional)
See docs/engineering/EVALS.md for the labelling guide.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from licita.config import ROOT, RUNS_DIR
from licita.errors import LicitaError
from licita.evals.metrics import MIN_N_FOR_PR, Rate, cohen_kappa, ground_counts, holding_matches, percentile
from licita.llm import pricing
from licita.rulings import extract
from licita.rulings.classify import outcome as tribunal_outcome

DEFAULT_GOLDEN = ROOT / "private" / "golden" / "rulings.jsonl"


class EvalError(LicitaError):
    pass


def golden_path(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get("LICITA_GOLDEN") or DEFAULT_GOLDEN)


def load_golden(path: Path) -> list[dict]:
    if not path.is_file():
        raise EvalError(f"no golden set at {path}", "evaluation needs hand-labelled rulings",
                        "label 30-50 rulings following docs/engineering/EVALS.md, or pass --golden FILE")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise EvalError(f"golden set {path} is empty", "no labelled rulings", "add labelled rulings, one per line")
    bad = [r.get("id") for r in rows if "outcome" not in r or "grounds" not in r]
    if bad:
        raise EvalError("golden rows without labels", f"ids {bad[:5]}", "every row needs outcome and grounds")
    return rows


@dataclass
class ModelScore:
    model: str
    n_gold: int
    valid: Rate = field(default_factory=lambda: Rate(0, 0))
    outcome: Rate = field(default_factory=lambda: Rate(0, 0))
    outcome_vs_tribunal: Rate = field(default_factory=lambda: Rate(0, 0))
    precision: Rate = field(default_factory=lambda: Rate(0, 0))
    recall: Rate = field(default_factory=lambda: Rate(0, 0))
    holding: Rate = field(default_factory=lambda: Rate(0, 0))
    quotes: Rate = field(default_factory=lambda: Rate(0, 0))
    usd_per_1000: float | None = None
    p50_s: float | None = None
    p95_s: float | None = None
    statuses: dict[str, int] = field(default_factory=dict)

    def row(self) -> list[str]:
        small = self.valid.k < MIN_N_FOR_PR
        pr = (lambda r: f"n<{MIN_N_FOR_PR}") if small else (lambda r: r.fmt())
        money = "–" if self.usd_per_1000 is None else f"${self.usd_per_1000:,.2f}"
        lat = "–" if self.p50_s is None else f"{self.p50_s:.1f}s / {self.p95_s:.1f}s"
        return [self.model, self.valid.fmt(), self.outcome.fmt(), pr(self.precision), pr(self.recall),
                pr(self.holding), self.quotes.fmt(), money, lat]


HEADER = ["model", "valid output", "outcome", "ground precision", "ground recall", "decision per ground",
          "quotes verified", "cost / 1,000 rulings", "latency p50 / p95"]


def score_model(con, model: str, gold: list[dict]) -> ModelScore:
    s = ModelScore(model, len(gold))
    tp = npred = ngold = hc = hn = qk = qn = ok_out = tri_k = tri_n = 0
    costs: list[float] = []
    latencies: list[float] = []
    table = pricing.load()
    attempted = 0
    for g in gold:
        row = con.execute("""select e.status, e.data, e.input_tokens, e.output_tokens, e.latency_s, e.batch,
                                    r.tipo_resolucion
                             from ruling_extractions e left join rulings r on r.id = e.id
                             where e.id = ? and e.model = ?""", [g["id"], model]).fetchone()
        if row is None:
            continue
        attempted += 1
        status, data, tin, tout, lat, batch, tipo = row
        s.statuses[status] = s.statuses.get(status, 0) + 1
        if lat is not None and not batch:
            latencies.append(lat)
        if (c := table.cost(model, tin, tout, batch=bool(batch))) is not None:
            costs.append(c)
        if status != "done":
            continue
        pred = json.loads(data)
        ok_out += int(pred["outcome"] == g["outcome"])
        if tipo:
            tri_n += 1
            tri_k += int(pred["outcome"] == tribunal_outcome(tipo))
        a, b, c3 = ground_counts(g["grounds"], pred["grounds"])
        tp, npred, ngold = tp + a, npred + b, ngold + c3
        k, n = holding_matches(g["grounds"], pred["grounds"])
        hc, hn = hc + k, hn + n
        q = con.execute("select count(quote), count(*) filter (where quote_verified) from ruling_grounds "
                        "where id = ? and model = ?", [g["id"], model]).fetchone()
        qn, qk = qn + q[0], qk + q[1]
    done = s.statuses.get("done", 0)
    s.valid = Rate(done, attempted)
    s.outcome = Rate(ok_out, done)
    s.outcome_vs_tribunal = Rate(tri_k, tri_n)
    s.precision, s.recall, s.holding = Rate(tp, npred), Rate(tp, ngold), Rate(hc, hn)
    s.quotes = Rate(qk, qn)
    s.usd_per_1000 = (sum(costs) / len(costs) * 1000) if costs else None
    s.p50_s, s.p95_s = percentile(latencies, 0.5), percentile(latencies, 0.95)
    return s


def agreement(gold: list[dict]) -> str:
    pairs = [(g["outcome"], g["second"]["outcome"]) for g in gold if g.get("second")]
    if not pairs:
        return "No double-labelled rulings yet: inter-annotator agreement not measured."
    a, b = zip(*pairs, strict=True)
    k = cohen_kappa(list(a), list(b))
    same = Rate(sum(x == y for x, y in pairs), len(pairs))
    return f"Double-labelled rulings: {len(pairs)}. Outcome agreement {same.fmt()}, Cohen's kappa {k:.2f}."


def models_in_db(con, gold: list[dict]) -> list[str]:
    ids = [g["id"] for g in gold]
    return [m for (m,) in con.execute("select distinct model from ruling_extractions where list_contains(?, id) "
                                      "order by model", [ids]).fetchall()]


def report(con, gold: list[dict], models: list[str] | None = None, title: str = "Ruling extraction") -> str:
    models = models or models_in_db(con, gold)
    if not models:
        raise EvalError("no model has extractions for the golden rulings", "nothing to score",
                        "run `python -m licita.evals.run live --models ...` (needs MISTRAL_API_KEY)")
    scores = [score_model(con, m, gold) for m in models]
    lines = [f"# {title}", "", f"Golden set: {len(gold)} rulings. Intervals are Wilson 95%. "
             f"Precision/recall/decision accuracy need at least {MIN_N_FOR_PR} valid outputs.", "",
             "| " + " | ".join(HEADER) + " |", "|" + "---|" * len(HEADER)]
    lines += ["| " + " | ".join(s.row()) + " |" for s in scores]
    lines += ["", "Outcome agreement with the tribunal's own published label (independent of our labels):"]
    lines += [f"- {s.model}: {s.outcome_vs_tribunal.fmt()}" for s in scores]
    lines += ["", "Status counts: " + "; ".join(f"{s.model} {s.statuses}" for s in scores),
              "", agreement(gold), "", f"Cost: {pricing.load().caveat}. Batch rows use the batch discount.",
              f"Prompt hash: {extract.PROMPT_HASH}."]
    return "\n".join(lines)


def write_report(text: str, out_dir: Path = RUNS_DIR / "evals") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"evals_{datetime.now():%Y%m%d-%H%M%S}.md"
    path.write_text(text + "\n")
    return path


def live(models: list[str], gold: list[dict], con=None) -> list[str]:
    """Call each model synchronously on the golden rulings it has not answered yet (costs money)."""
    con = con or extract._db()
    ids = {g["id"] for g in gold}
    missing = ids - {r[0] for r in con.execute("select id from ruling_texts where status = 'done'").fetchall()}
    if missing:
        raise EvalError(f"{len(missing)} golden rulings have no text", f"e.g. {sorted(missing)[:3]}",
                        "run `python -m licita.rulings.pdfs` first, or fix the ids in the golden set")
    pinned = []
    for m in models:
        out = extract.submit("all", None, model=m, sync=True, con=con, ids=ids)
        pinned.append(out["model"])
    return pinned


def replay(fixture_dir: Path) -> str:
    """Offline evaluation for CI: synthetic rulings + cached model responses -> temp database -> report.

    fixture_dir holds rulings.jsonl ({id, acto, tipo_resolucion, text}), responses.jsonl ({id, model,
    prompt_hash, response: <chat completion body>}) and golden.jsonl.
    """
    gold = load_golden(fixture_dir / "golden.jsonl")
    with tempfile.TemporaryDirectory() as tmp:
        con = extract._db(Path(tmp) / "replay.duckdb")
        for r in map(json.loads, (fixture_dir / "rulings.jsonl").read_text().splitlines()):
            con.execute("insert into rulings (id, acto, tipo_resolucion, year, num) values (?, ?, ?, 0, 0)",
                        [r["id"], r["acto"], r.get("tipo_resolucion")])
            con.execute("insert into ruling_texts (id, status, text) values (?, 'done', ?)", [r["id"], r["text"]])
        stale, models = 0, []
        from licita.llm.base import parse_completion
        for rec in map(json.loads, (fixture_dir / "responses.jsonl").read_text().splitlines()):
            stale += int(rec.get("prompt_hash") != extract.PROMPT_HASH)
            res = parse_completion(rec["response"], latency_s=rec.get("latency_s"))
            extract.store_completion(con, rec["id"], rec["model"], "replay", res, provider="replay",
                                     prompt_hash=rec.get("prompt_hash"), batch=False)
            if rec["model"] not in models:
                models.append(rec["model"])
        text = report(con, gold, models, title="Ruling extraction (offline replay of cached outputs)")
        con.close()
    if stale:
        text += f"\n\nNote: {stale} cached responses were produced with a different prompt (hash mismatch)."
    readme = fixture_dir / "README.md"
    if readme.is_file() and "synthetic" in readme.read_text().lower():
        text = "> **SYNTHETIC FIXTURES: invented rulings and responses. These numbers are not model results.**\n\n" + text
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate ruling extraction against the golden set")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--golden")
    s.add_argument("--models", help="comma-separated; default: every model with rows for the golden ids")
    s.add_argument("--no-write", action="store_true")
    lv = sub.add_parser("live")
    lv.add_argument("--models", required=True)
    lv.add_argument("--golden")
    rp = sub.add_parser("replay")
    rp.add_argument("fixture_dir", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "replay":
            print(replay(args.fixture_dir))
            return 0
        gold = load_golden(golden_path(args.golden))
        models = [m.strip() for m in args.models.split(",")] if args.models else None
        if args.cmd == "live":
            models = live(models or [], gold)
        text = report(extract._db(), gold, models)
        print(text)
        if args.cmd == "live" or not args.no_write:
            print(f"\nreport -> {write_report(text)}")
    except LicitaError as e:
        print(e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
