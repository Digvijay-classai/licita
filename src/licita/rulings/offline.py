"""Open-model baseline: run the ruling extraction on free Kaggle GPUs, then import the results.

  python -m licita.rulings.offline export [--scope pliegos|all]   # -> data/kaggle/
  python -m licita.rulings.offline import results.jsonl           # -> rulings.duckdb

The export carries the exact system prompt, JSON schema and user envelope used for the Mistral run
(licita.rulings.extract), so the comparison isolates the model. Imported rows go through the same
schema validation and quote verification, keyed by model, next to the Mistral extractions.
For a self-hosted model on your own GPU, prefer LICITA_LLM_PROVIDER=local with `extract submit` instead.
Ruling texts are public tribunal documents; nothing hospital-specific is exported.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from licita import __version__
from licita.config import DATA_DIR, ROOT
from licita.rulings.extract import (
    PROMPT_HASH,
    SCHEMA,
    SYSTEM,
    _db,
    pending,
    store_failure,
    store_result,
    user_content,
)

OUT_DIR = DATA_DIR / "kaggle"
RUNNER = ROOT / "kaggle" / "extract_qwen.py"
BASELINE_MODEL = "ollama/qwen3:14b"

SETUP_CELL = """# 1) Install Ollama (needs Settings -> Internet: on, and Accelerator: GPU T4 x2)
!apt-get -qq install -y zstd > /dev/null
!curl -fsSL https://ollama.com/install.sh | sh"""

CONFIG_CELL = """# 2) Smoke test first: 5 rulings. Then set LICITA_LIMIT to "0" and run all cells again.
#    Finished rulings are skipped on re-runs, so a cut session can simply be restarted.
#    LICITA_THINK="1" lets Qwen3 reason before answering (slower, usually more accurate).
import os
os.environ["LICITA_LIMIT"] = "5"
os.environ["LICITA_THINK"] = "1\""""


def notebook(runner_source: str) -> dict:
    def code(src: str) -> dict:
        return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                "source": src.splitlines(keepends=True)}
    intro = ("# Licita: open-model baseline extraction\n"
             "Qwen3-14B on Kaggle T4 x2 via Ollama, with the same prompt and JSON schema as the Mistral run.\n"
             "Output: `/kaggle/working/results.jsonl` (download it from the Output panel).")
    return {"nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                         "language_info": {"name": "python"}},
            "cells": [{"cell_type": "markdown", "metadata": {}, "source": intro.splitlines(keepends=True)},
                      code(SETUP_CELL), code(CONFIG_CELL), code("# 3) Run\n" + runner_source)]}


def export(scope: str, out_dir: Path = OUT_DIR) -> Path:
    con = _db()
    todo = pending(con, scope, BASELINE_MODEL)
    ds = out_dir / "licita-rulings"
    ds.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"id": rid, "user": user_content(text)} for rid, text in todo]).to_parquet(
        ds / "jobs.parquet", index=False)
    (ds / "manifest.json").write_text(json.dumps(
        {"system": SYSTEM, "schema": SCHEMA, "prompt_hash": PROMPT_HASH, "scope": scope, "n": len(todo), "licita_version": __version__,
         "exported_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False, indent=1))
    nb = out_dir / "licita_extract_qwen.ipynb"
    nb.write_text(json.dumps(notebook(RUNNER.read_text()), ensure_ascii=False, indent=1))
    print(f"{len(todo)} rulings -> {ds}/ (upload as a private Kaggle dataset)\nnotebook -> {nb}")
    return ds


def import_results(path: Path, con=None) -> dict:
    con = con or _db()
    run_id = f"kaggle:{path.name}:{datetime.now():%Y%m%d-%H%M%S}"
    totals = {"succeeded": 0, "failed": 0, "invalid": 0, "grounds": 0, "duplicates": 0, "quotes": 0,
              "quotes_verified": 0}
    seconds = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["status"] != "succeeded":
            store_failure(con, rec["id"], rec["model"], run_id, rec["status"], rec.get("error") or "",
                          provider="kaggle", prompt_hash=rec.get("prompt_hash"), batch=False)
            totals["failed"] += 1
            continue
        n = store_result(con, rec["id"], rec["model"], run_id, rec["data"],
                         rec.get("input_tokens"), rec.get("output_tokens"), provider="kaggle",
                         prompt_hash=rec.get("prompt_hash"), latency_s=rec.get("seconds"), batch=False)
        for k in ("invalid", "grounds", "duplicates", "quotes", "quotes_verified"):
            totals[k] += n[k]
        totals["succeeded"] += 1 - n["invalid"]
        if rec.get("seconds"):
            seconds.append(rec["seconds"])
    if seconds:
        totals["median_seconds"] = sorted(seconds)[len(seconds) // 2]
    print(totals)
    return totals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--scope", choices=["pliegos", "all"], default="pliegos")
    i = sub.add_parser("import")
    i.add_argument("path", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "export":
        export(args.scope)
    else:
        import_results(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
