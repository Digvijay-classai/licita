"""Paths and runtime settings. Everything data-related lives outside git (see .gitignore)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("LICITA_DATA", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"          # transient zips (deleted after parsing)
PARQUET_DIR = DATA_DIR / "parquet"  # parsed PLACSP snapshots
STATE_DIR = DATA_DIR / "state"      # sizes, logs, job tables
SPINES_DIR = Path(os.environ.get("LICITA_SPINES", ROOT / "spines"))  # versioned spines + spine.duckdb symlink
RUNS_DIR = Path(os.environ.get("LICITA_RUNS", ROOT / "runs"))        # run_id logs and build reports
OUT_DIR = Path(os.environ.get("LICITA_OUT", ROOT / "out"))           # per-buyer fact sheets (never committed)
DEMO = os.environ.get("LICITA_DEMO") == "1"                           # synthetic data, no live model

# Buyers, region and tribunal are deployment settings: see licita.profile and profiles/example.toml.
