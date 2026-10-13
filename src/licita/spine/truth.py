"""Ground truth and outcomes: what a procurement team tells us that PLACSP never publishes.

Two CSV families live outside git under `$LICITA_DATA/truth/` (templates in `spine/templates/`):

  <BUYER_KEY>_extensions.csv   per contract: is it extended, replaced, pending, lapsed, no longer needed
  outcomes.csv                 what happened after a memo: file opened in time, consultation run, bids received

`licita truth validate` checks the files, `licita truth import` shows what the radar would change (read-only),
`licita truth template` writes a template, `licita truth outcomes` prints the outcome summary. The build
loads both into the spine (`truth`, `outcomes`) and the radar lets the team's answer override the heuristic
successor match (see `licita.spine.radar.apply_truth`); the agreement between the two is the matcher's
measured precision.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from licita.config import DATA_DIR, SPINES_DIR
from licita.errors import LicitaError

TRUTH_DIR = DATA_DIR / "truth"
TEMPLATES = Path(__file__).with_name("templates")

STATUSES = ("extended", "extension_pending", "replaced", "replaced_by_framework", "in_progress", "lapsed",
            "ended_no_need")
TRUTH_COLUMNS = ["buyer_key", "folder_id", "status", "successor_folder_id", "extended_until", "note", "source",
                 "recorded_on"]
TRUTH_REQUIRED = ("buyer_key", "folder_id", "status", "source", "recorded_on")

EVENTS = ("memo_delivered", "file_opened_before_start_by", "market_consultation_run", "decision_changed",
          "tender_published", "lots_received_bids", "extension_avoided", "other")
OUTCOME_COLUMNS = ["recorded_on", "buyer_key", "folder_id", "event", "value_eur", "bids", "note", "source"]
OUTCOME_REQUIRED = ("recorded_on", "buyer_key", "event", "source")


def _read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, comment="#", skip_blank_lines=True, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    df = df[columns].apply(lambda s: s.str.strip())
    df["src_file"] = path.name
    df["src_line"] = range(2, len(df) + 2)       # header is line 1; comment lines are not counted by pandas
    return df


def _date_ok(v: str) -> bool:
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _fail(problems: list[str], what: str, where: Path) -> None:
    shown = "\n  ".join(problems[:12]) + (f"\n  … {len(problems) - 12} more" if len(problems) > 12 else "")
    raise LicitaError(f"{len(problems)} invalid row(s) in {what} files under {where}",
                      "a value does not match the schema (see the template's comment line)",
                      f"fix the rows below, then rerun `licita truth validate`:\n  {shown}")


def validate_truth(df: pd.DataFrame) -> list[str]:
    problems = []
    for r in df.itertuples():
        at = f"{r.src_file}:{r.src_line}"
        for col in TRUTH_REQUIRED:
            if not getattr(r, col):
                problems.append(f"{at}: {col} is empty")
        if r.status and r.status not in STATUSES:
            problems.append(f"{at}: status {r.status!r} not in {', '.join(STATUSES)}")
        if r.recorded_on and not _date_ok(r.recorded_on):
            problems.append(f"{at}: recorded_on {r.recorded_on!r} is not an ISO date")
        if r.extended_until and not _date_ok(r.extended_until):
            problems.append(f"{at}: extended_until {r.extended_until!r} is not an ISO date")
        if r.status == "extended" and not r.extended_until:
            problems.append(f"{at}: status extended needs extended_until")
    dup = df[df.duplicated(["buyer_key", "folder_id"], keep=False)]
    for (b, f), g in dup.groupby(["buyer_key", "folder_id"]):
        problems.append(f"{g.src_file.iloc[0]}: ({b}, {f}) appears {len(g)} times; keep the latest answer only")
    return problems


def load_truth(data_dir: Path | None = None) -> pd.DataFrame:
    """Every `*_extensions.csv` under data_dir, validated. Empty frame (full schema) when there is none."""
    d = Path(data_dir) if data_dir else TRUTH_DIR
    files = sorted(d.glob("*_extensions.csv")) if d.is_dir() else []
    if not files:
        return pd.DataFrame(columns=TRUTH_COLUMNS)
    df = pd.concat([_read_csv(f, TRUTH_COLUMNS) for f in files], ignore_index=True)
    problems = validate_truth(df)
    if problems:
        _fail(problems, "extension ground-truth", d)
    out = df[TRUTH_COLUMNS].replace({"": None}).copy()
    out["extended_until"] = pd.to_datetime(out["extended_until"]).dt.date
    out["recorded_on"] = pd.to_datetime(out["recorded_on"]).dt.date
    return out


def validate_outcomes(df: pd.DataFrame) -> list[str]:
    problems = []
    for r in df.itertuples():
        at = f"{r.src_file}:{r.src_line}"
        for col in OUTCOME_REQUIRED:
            if not getattr(r, col):
                problems.append(f"{at}: {col} is empty")
        if r.event and r.event not in EVENTS:
            problems.append(f"{at}: event {r.event!r} not in {', '.join(EVENTS)}")
        if r.recorded_on and not _date_ok(r.recorded_on):
            problems.append(f"{at}: recorded_on {r.recorded_on!r} is not an ISO date")
        for col in ("value_eur", "bids"):
            v = getattr(r, col)
            if v:
                try:
                    float(v.replace(",", "."))
                except ValueError:
                    problems.append(f"{at}: {col} {v!r} is not a number")
    return problems


def load_outcomes(data_dir: Path | None = None) -> pd.DataFrame:
    d = Path(data_dir) if data_dir else TRUTH_DIR
    path = d / "outcomes.csv"
    if not path.is_file():
        return pd.DataFrame(columns=OUTCOME_COLUMNS)
    df = _read_csv(path, OUTCOME_COLUMNS)
    problems = validate_outcomes(df)
    if problems:
        _fail(problems, "outcome", d)
    out = df[OUTCOME_COLUMNS].replace({"": None}).copy()
    out["recorded_on"] = pd.to_datetime(out["recorded_on"]).dt.date
    for col in ("value_eur", "bids"):
        out[col] = pd.to_numeric(out[col].astype("string").str.replace(",", ".", regex=False), errors="coerce")
    return out


_TRUTH_DDL = ("buyer_key varchar, folder_id varchar, status varchar, successor_folder_id varchar, "
              "extended_until date, note varchar, source varchar, recorded_on date")
_OUTCOME_DDL = ("recorded_on date, buyer_key varchar, folder_id varchar, event varchar, value_eur double, "
                "bids double, note varchar, source varchar")


def write_table(con: duckdb.DuckDBPyConnection, df: pd.DataFrame, name: str = "truth") -> int:
    """Materialise a validated frame as `truth` or `outcomes` (typed, so an empty frame still has a schema)."""
    ddl = _TRUTH_DDL if name == "truth" else _OUTCOME_DDL
    cols = TRUTH_COLUMNS if name == "truth" else OUTCOME_COLUMNS
    con.execute(f"create or replace table {name} ({ddl})")
    if len(df):
        con.register(f"{name}_df", df[cols])
        con.execute(f"insert into {name} select * from {name}_df")
        con.unregister(f"{name}_df")
    return int(len(df))


def summary(outcomes: pd.DataFrame) -> pd.DataFrame:
    """Per buyer, month and event: how many, and the € they touched."""
    if outcomes.empty:
        return pd.DataFrame(columns=["buyer_key", "month", "event", "n", "value_eur"])
    df = outcomes.copy()
    df["month"] = pd.to_datetime(df["recorded_on"]).dt.strftime("%Y-%m")
    df["value_eur"] = pd.to_numeric(df["value_eur"], errors="coerce").fillna(0.0)
    out = (df.groupby(["buyer_key", "month", "event"], as_index=False)
             .agg(n=("event", "size"), value_eur=("value_eur", "sum")))
    return out.sort_values(["buyer_key", "month", "event"]).reset_index(drop=True)


def _cmd_validate(d: Path) -> int:
    try:
        truth = load_truth(d)
        outcomes = load_outcomes(d)
    except LicitaError as e:
        print(e, file=sys.stderr)
        return 1
    if truth.empty:
        print(f"no *_extensions.csv under {d}")
    else:
        print("extension ground truth:")
        print(truth.groupby(["buyer_key", "status"]).size().rename("rows").to_string())
    print(f"outcomes: {len(outcomes)} row(s)")
    return 0


def _cmd_template(kind: str, out: Path) -> int:
    src = TEMPLATES / ("ground_truth_extensions.csv" if kind == "extensions" else "outcomes.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        print(f"{out} exists; not overwritten", file=sys.stderr)
        return 1
    out.write_text(src.read_text())
    print(f"template -> {out}")
    return 0


def _cmd_import(d: Path, spine: Path) -> int:
    """Read-only preview: which radar rows the truth would change and how. The build applies it."""
    from licita.spine.radar import apply_truth, matcher_precision
    try:
        truth = load_truth(d)
    except LicitaError as e:
        print(e, file=sys.stderr)
        return 1
    if truth.empty:
        print(f"no ground truth under {d}; nothing to preview")
        return 0
    if not spine.exists():
        print(f"no spine at {spine}; `make build` first", file=sys.stderr)
        return 1
    with duckdb.connect(str(spine), read_only=True) as con:
        radar = con.execute("select * from radar").df()
        as_of = con.execute("select as_of from meta").fetchone()[0]
    after = apply_truth(radar, truth, as_of)
    hit = after[after.truth_status.notna()]
    print(f"{len(truth)} truth row(s); {len(hit)} match a radar contract "
          f"({len(truth) - len(hit)} name folders the radar does not list)")
    if len(hit):
        moves = hit.groupby(["heuristic_flag", "flag"]).size().rename("contracts")
        print("flag changes (heuristic -> with truth):")
        print(moves.to_string())
        print("matcher agreement where the team answered:")
        for k, v in matcher_precision(after).items():
            print(f"  {k}: {v}")
    print("preview only: `make build` writes it into the spine and the radar")
    return 0


def _cmd_outcomes(d: Path) -> int:
    try:
        outcomes = load_outcomes(d)
    except LicitaError as e:
        print(e, file=sys.stderr)
        return 1
    s = summary(outcomes)
    print(s.to_string(index=False) if len(s) else f"no outcomes.csv under {d}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="licita truth", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=None, help=f"ground-truth folder (default {TRUTH_DIR})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="check every CSV and count rows per buyer")
    t = sub.add_parser("template", help="write a CSV template")
    t.add_argument("kind", choices=["extensions", "outcomes"])
    t.add_argument("--out", type=Path, default=None)
    i = sub.add_parser("import", help="preview what the radar would change (read-only)")
    i.add_argument("--spine", type=Path, default=SPINES_DIR / "spine.duckdb")
    sub.add_parser("outcomes", help="outcome summary per buyer, month and event")
    args = ap.parse_args(argv)
    d = args.dir or TRUTH_DIR
    if args.cmd == "validate":
        return _cmd_validate(d)
    if args.cmd == "template":
        default = d / ("BUYER_extensions.csv" if args.kind == "extensions" else "outcomes.csv")
        return _cmd_template(args.kind, args.out or default)
    if args.cmd == "import":
        return _cmd_import(d, args.spine)
    return _cmd_outcomes(d)


if __name__ == "__main__":
    raise SystemExit(main())
