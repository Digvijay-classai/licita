"""Prepare a golden-set sample for hand labelling (the labels themselves are human work).

  python -m licita.evals.label sample [--n 40] [--seed 1]   -> private/golden/to_label.jsonl
  python -m licita.evals.label kit [--labeller A] [--n 40]  -> private/golden/kit_<L>/ (sheets + answer sheet)
  python -m licita.evals.label import ANSWERS.csv --labeller A   -> private/golden/rulings.jsonl

Draws rulings that challenge tender documents, stratified by the tribunal's published outcome so dismissed,
upheld and partly upheld rulings are all represented. Each line carries the id, the tribunal's label (an
independent check, not the answer), the text length and empty label fields to fill in. When a line is
labelled, move it to private/golden/rulings.jsonl. Guide: docs/engineering/EVALS.md.

`kit` is for a domain expert who does not use the command line: one plain-text sheet per ruling (operative
part first, then the full text), a Spanish guide and a spreadsheet (`;`-separated, opens in Spanish Excel)
with Spanish answer values. It never contains model output or the tribunal's metadata label. `import`
checks the filled sheet, reports every error with its row, and merges it into the golden set: labeller A
rows become golden rows, any other labeller's rows are attached as `second` for the agreement statistic.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import shutil
from collections import defaultdict
from pathlib import Path

from licita.config import ROOT
from licita.errors import LicitaError
from licita.rulings import extract
from licita.rulings.classify import challenges_pliegos
from licita.rulings.classify import outcome as tribunal_outcome

OUT = ROOT / "private" / "golden" / "to_label.jsonl"
GOLDEN = ROOT / "private" / "golden" / "rulings.jsonl"
GUIDE = ROOT / "docs" / "engineering" / "LABELLING_es.md"

OUTCOMES_ES = {"estimado": "upheld", "estimado_parcial": "partly_upheld", "desestimado": "dismissed",
               "inadmitido": "inadmissible", "otro": "other"}
HOLDINGS_ES = {"estimado": "upheld", "desestimado": "dismissed", "no_resuelto": "not_decided"}
COLUMNS = ["hoja", "resolucion", "resultado", "motivo", "categoria", "decision", "notas"]
_DISPOSITIVE = re.compile(r"\n[^\n]{0,120}\b(ACUERDA|RESUELVE)\s*:?\s*\n", re.I)


class LabelError(LicitaError):
    pass


def operative_part(text: str, limit: int = 4000) -> str | None:
    """The fallo: text after the last ACUERDA/RESUELVE heading (rulings put it at the end)."""
    hits = list(_DISPOSITIVE.finditer(text))
    return text[hits[-1].start():][:limit].strip() if hits else None


def write_kit(con, sample_rows: list[dict], out_dir: Path) -> Path:
    """Reading sheets + guide + empty answer sheet. Sheets carry no model output and no tribunal label."""
    out_dir.mkdir(parents=True, exist_ok=True)
    answers = []
    for i, r in enumerate(sample_rows, 1):
        text = con.execute("select text from ruling_texts where id = ?", [r["id"]]).fetchone()[0]
        fallo = operative_part(text) or "(no se encontró el encabezado ACUERDA/RESUELVE: buscarlo al final del texto)"
        (out_dir / f"{i:02d}.txt").write_text(
            f"HOJA {i:02d} · Resolución {r['id']}\n\n== PARTE DISPOSITIVA (para el resultado) ==\n\n{fallo}\n\n"
            f"== TEXTO COMPLETO (para los motivos) ==\n\n{text}\n", encoding="utf-8")
        answers.append({"hoja": f"{i:02d}", "resolucion": r["id"]})
    with (out_dir / "respuestas.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(answers)
    if GUIDE.is_file():
        shutil.copy(GUIDE, out_dir / "GUIA.md")
    return out_dir


def read_answers(path: Path, labeller: str) -> list[dict]:
    """Parse a filled answer sheet (`;` or `,`). Raises LabelError listing every problem with its row."""
    raw = path.read_text(encoding="utf-8-sig")
    delim = ";" if raw.splitlines()[0].count(";") >= raw.splitlines()[0].count(",") else ","
    rows = list(csv.DictReader(raw.splitlines(), delimiter=delim))
    missing = set(COLUMNS[1:6]) - set(rows[0] if rows else {})
    if missing:
        raise LabelError(f"answer sheet {path.name} lacks columns {sorted(missing)}", "header changed",
                         f"keep the header row: {';'.join(COLUMNS)}")
    errors: list[str] = []
    by_id: dict[str, dict] = {}
    norm = lambda v: (v or "").strip().lower().replace(" ", "_")  # noqa: E731
    for n, row in enumerate(rows, 2):
        rid, res = (row.get("resolucion") or "").strip(), norm(row.get("resultado"))
        cat, dec = (row.get("categoria") or "").strip(), norm(row.get("decision"))
        if not rid:
            continue
        if not res and not cat and not dec:
            continue                                  # not labelled yet
        if res not in OUTCOMES_ES:
            errors.append(f"row {n}: resultado {res!r} not one of {sorted(OUTCOMES_ES)}")
            continue
        g = by_id.setdefault(rid, {"id": rid, "outcome": OUTCOMES_ES[res], "grounds": [], "labeller": labeller,
                                   "notes": []})
        if g["outcome"] != OUTCOMES_ES[res]:
            errors.append(f"row {n}: resultado differs from an earlier row of the same ruling")
        if cat or dec:
            if cat not in extract.GROUNDS:
                errors.append(f"row {n}: categoria {cat!r} not one of the categories in GUIA.md")
            if dec not in HOLDINGS_ES:
                errors.append(f"row {n}: decision {dec!r} not one of {sorted(HOLDINGS_ES)}")
            if cat in extract.GROUNDS and dec in HOLDINGS_ES:
                g["grounds"].append({"ground": cat, "holding": HOLDINGS_ES[dec]})
        if (note := (row.get("notas") or "").strip()):
            g["notes"].append(note)
    if errors:
        raise LabelError(f"{len(errors)} problem(s) in {path.name}", "; ".join(errors[:20]),
                         "correct those rows and import again (nothing was written)")
    for g in by_id.values():
        g["notes"] = " | ".join(g["notes"])
    return list(by_id.values())


def merge_golden(labelled: list[dict], golden: Path = GOLDEN) -> tuple[int, int]:
    """Labeller A rows replace golden rows by id; other labellers attach as `second`. Returns (new, second)."""
    rows = {}
    if golden.is_file():
        rows = {r["id"]: r for r in map(json.loads, golden.read_text(encoding="utf-8").splitlines()) if r}
    new = second = 0
    for g in labelled:
        if g["labeller"] == "A":
            new += g["id"] not in rows
            keep = rows.get(g["id"], {}).get("second")
            rows[g["id"]] = {**g, **({"second": keep} if keep else {})}
        elif g["id"] in rows:
            rows[g["id"]]["second"] = {k: g[k] for k in ("outcome", "grounds", "labeller", "notes")}
            second += 1
        else:
            raise LabelError(f"labeller {g['labeller']} labelled {g['id']}, which labeller A has not",
                             "the second label is only used for agreement", "import labeller A's sheet first")
    golden.parent.mkdir(parents=True, exist_ok=True)
    golden.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()), encoding="utf-8")
    return new, second


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
    ap = argparse.ArgumentParser(description="golden-set sampling, labelling kit and answer import")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=40)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--out", type=Path, default=OUT)
    k = sub.add_parser("kit", help="reading sheets + answer sheet for a domain expert")
    k.add_argument("--labeller", default="A")
    k.add_argument("--n", type=int, help="first N sampled rulings (e.g. 10 for the second labeller)")
    k.add_argument("--sample", type=Path, default=OUT)
    i = sub.add_parser("import", help="check a filled answer sheet and merge it into the golden set")
    i.add_argument("answers", type=Path)
    i.add_argument("--labeller", default="A")
    i.add_argument("--golden", type=Path, default=GOLDEN)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "sample":
            rows = sample(extract._db(), args.n, args.seed)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
            print(f"{len(rows)} rulings to label -> {args.out}")
        elif args.cmd == "kit":
            if not args.sample.is_file():
                raise LabelError(f"no sample at {args.sample}", "the kit is built from the sample", "run `make label`")
            rows = [json.loads(x) for x in args.sample.read_text(encoding="utf-8").splitlines() if x.strip()]
            out = write_kit(extract._db(), rows[: args.n] if args.n else rows,
                            args.sample.parent / f"kit_{args.labeller}")
            print(f"labelling kit for {args.labeller}: {len(rows[: args.n] if args.n else rows)} sheets -> {out}")
        else:
            got = read_answers(args.answers, args.labeller)
            new, second = merge_golden(got, args.golden)
            print(f"{len(got)} labelled rulings read; {new} new golden rows, {second} second labels -> {args.golden}")
    except LicitaError as e:
        print(e)
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
