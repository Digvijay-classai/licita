"""`licita <command>`: one entry point for every task. Each command is the module of the same job.

Run `licita` with no arguments for the list, `licita <command> --help` for its options.
"""
from __future__ import annotations

import importlib
import os
import runpy
import subprocess
import sys

from licita.config import ROOT
from licita.errors import LicitaError

COMMANDS: dict[str, tuple[str, str]] = {
    # first run
    "demo": ("licita.demo", "synthetic data through the real pipeline, no key: `licita demo app`"),
    "config": ("licita.cli:config", "show the effective profile, provider, models and paths"),
    "profile": ("licita.profile", "show the deployment profile (or `profile buyers`)"),
    "app": ("licita.cli:app", "open the app for one buyer: `licita app [BUYER]`"),
    # data
    "ingest": ("licita.ingest.backfill", "download and parse PLACSP open data (~10 GB, resumable)"),
    "status": ("licita.ingest.status", "backfill progress (`status -w` to watch)"),
    "sync": ("licita.ingest.sync", "fetch changed PLACSP archives, rebuild the spine if needed (schedulable)"),
    "build": ("licita.spine.build", "build a versioned spine and swap it in atomically"),
    "dq": ("licita.spine.dq", "data-quality report on the live spine (the gate every build must pass)"),
    "facts": ("licita.memo.facts", "fact sheets for one buyer"),
    "truth": ("licita.spine.truth", "procurement-team ground truth and outcome log (validate | import | template | outcomes)"),
    "export": ("licita.export.ocds", "OCDS 1.1 JSONL export of the health slice, with a datasheet"),
    "documents": ("licita.memo.documents", "fetch current tender documents for fact-sheet contracts"),
    # rulings and models
    "scrape": ("licita.rulings.scrape", "scrape the profile's tribunal rulings (resumable)"),
    "pdfs": ("licita.rulings.pdfs", "download ruling PDFs and extract text (`--ocr` for scans, needs key)"),
    "extract": ("licita.rulings.extract", "extract grounds and holdings with Mistral (submit | collect | status)"),
    "evals": ("licita.evals.run", "score models on the golden set (score | live | replay)"),
    "label": ("licita.evals.label", "sample rulings for hand labelling"),
    "baseline": ("licita.rulings.offline", "open-model baseline on Kaggle GPUs (export | import)"),
    # hygiene
    "anon": ("licita.anon", "check the tree names no buyer, region or tribunal"),
}


def usage() -> str:
    width = max(map(len, COMMANDS))
    rows = "\n".join(f"  {name:<{width}}  {desc}" for name, (_, desc) in COMMANDS.items())
    return f"usage: licita <command> [args]\n\n{rows}\n\nNew here? `licita demo app` (no key, ~1 minute)."


def config(argv: list[str]) -> int:
    from licita.config import DATA_DIR, DEMO, OUT_DIR, SPINES_DIR
    from licita.llm import Task, get_provider, model_overrides, provider_name
    from licita.llm.pricing import load
    from licita.profile import load_profile

    p = load_profile()
    name = provider_name()
    prov = get_provider(name)
    overrides = model_overrides()
    print(f"profile   {p.name}  ({p.path})  buyers: {', '.join(p.buyers)}")
    base_url = getattr(prov, "base_url", None)
    print(f"provider  {name}" + (f"  base_url={base_url}" if base_url else ""))
    key = os.environ.get("MISTRAL_API_KEY")
    print(f"key       MISTRAL_API_KEY {'set' if key and key.strip() else 'NOT set (demo, tests and local provider work without it)'}")
    for task in Task:
        try:
            model = prov.model_for(task)
        except LicitaError:
            model = "(not available with this provider)"
        source = ("env" if os.environ.get(f"LICITA_MODEL_{task.name}") else "profile") if task in overrides else "default"
        print(f"model     {task.value:<8} {model}  [{source}]")
    print(f"paths     data={DATA_DIR}  spines={SPINES_DIR}  out={OUT_DIR}" + ("  (DEMO)" if DEMO else ""))
    print(f"prices    {load().caveat}")
    return 0


def app(argv: list[str]) -> int:
    env = dict(os.environ)
    if argv:
        env["LICITA_BUYER"] = argv[0]
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(ROOT / "src" / "licita" / "app" / "main.py")],
                           env=env)


def run(target: str, argv: list[str]) -> int:
    if ":" in target:
        mod, fn = target.split(":")
        return getattr(importlib.import_module(mod), fn)(argv)
    module = importlib.import_module(target)
    if hasattr(module, "main"):
        return module.main(argv) or 0
    sys.argv = [target, *argv]           # modules with only a __main__ block
    runpy.run_module(target, run_name="__main__")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    cmd, rest = args[0], args[1:]
    if cmd not in COMMANDS:
        print(f"unknown command {cmd!r}\n\n{usage()}", file=sys.stderr)
        return 2
    try:
        return run(COMMANDS[cmd][0], rest)
    except LicitaError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
