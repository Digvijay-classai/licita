"""Prepare a golden-set sample for hand labelling (the labels themselves are human work).

  python -m licita.evals.label sample [--n 40] [--seed 1]   -> private/golden/to_label.jsonl

Draws rulings that challenge tender documents, stratified by the tribunal's published outcome so dismissed,
upheld and partly upheld rulings are all represented. Each line carries the id, the tribunal's label (an
independent check, not the answer), the text length and empty label fields to fill in. When a line is
labelled, move it to private/golden/rulings.jsonl. Guide: docs/engineering/EVALS.md.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from licita.config import ROOT
from licita.rulings import extract
from licita.rulings.classify import challenges_pliegos
from licita.rulings.classify import outcome as tribunal_outcome

OUT = ROOT / "private" / "golden" / "to_label.jsonl"


def sample(con, n: int, seed: int = 1, exclude: set[str] | None = None) -> list[dict]:
    rows = con.execute("""select r.id, r.acto, r.tipo_resolucion, t.chars from rulings r join ruling_texts t using (id)
                          where t.status = 'done'""").fetchall()
    strata: dict[str, list[tuple]] = defaultdict(list)
    for rid, acto, tipo, chars in rows:
        if challenges_pliegos(acto) and rid not in (exclude or set()):
            strata[tribunal_outcome(tipo)].append((rid, tipo, chars))
    rng = random.Random(seed)
    for v in strata.values():
        rng.shuffle(v)
    picked: list[tuple] = []
    while len(picked) < n and any(strata.values()):
        for k in sorted(strata):
            if strata[k] and len(picked) < n:
                picked.append(strata[k].pop())
    return [{"id": rid, "tribunal_label": tipo, "chars": chars, "outcome": None, "grounds": [], "labeller": None}
            for rid, tipo, chars in picked]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="golden-set sampling")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=40)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    rows = sample(extract._db(), args.n, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    print(f"{len(rows)} rulings to label -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
