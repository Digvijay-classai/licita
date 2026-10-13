"""Demo mode: a synthetic procurement dataset run through the real pipeline, no download and no API key.

  python -m licita.demo build        generate demo/data (~6k lots) -> real spine build -> fact sheets
  python -m licita.demo app          build if needed, then open the app on the example profile

Every buyer, supplier, title and number is invented (fixed seed). The shapes follow the parsed PLACSP
schema so the same views, gates, radar and fact sheets run unchanged. Bid counts drive discounts the
way they do in the real data (more bidders, deeper discount) so the charts read sensibly; nothing in
the demo is a finding about real procurement.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from licita.config import ROOT

DEMO_DIR = ROOT / "demo"
DEMO_ENV = {
    "LICITA_PROFILE": "example",
    "LICITA_DATA": str(DEMO_DIR / "data"),
    "LICITA_SPINES": str(DEMO_DIR / "spines"),
    "LICITA_RUNS": str(DEMO_DIR / "runs"),
    "LICITA_OUT": str(DEMO_DIR / "out"),
    "LICITA_DEMO": "1",
}
AS_OF = date(2026, 10, 1)
SEED = 20261009

# (cpv8, title stem, typical net budget) — generic hospital purchases
CATEGORIES = [
    ("33141000", "Suministro de material fungible de curas", 60_000),
    ("33696500", "Suministro de reactivos de laboratorio", 250_000),
    ("50421000", "Mantenimiento de equipos de electromedicina", 180_000),
    ("33182000", "Suministro de material de hemodinámica", 400_000),
    ("85142000", "Servicio de fisioterapia a domicilio", 120_000),
    ("90911200", "Servicio de limpieza de dependencias", 900_000),
    ("98310000", "Servicio de lavandería y lencería", 350_000),
    ("33600000", "Suministro de medicamentos", 500_000),
    ("50700000", "Mantenimiento de instalaciones térmicas", 220_000),
    ("33190000", "Suministro de dispositivos médicos diversos", 90_000),
]
SUPPLIERS = [f"Suministros Ejemplo {c} SL" for c in "ABCDEFGHIJKLMNOPQRST"]


def _nif(prefix: str, i: int) -> str:
    return f"{prefix}00{i:05d}X"      # province code 00 does not exist: never a real NIF


def generate(seed: int = SEED, n_peers: int = 40, years: tuple[int, int] = (2018, 2026)) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    buyers = [("Q0000001A", "Hospital A"), ("Q0000002B", "Hospital B")] + [
        (_nif("Q", i), f"Hospital Comarcal de Ejemplo {i}") for i in range(3, n_peers + 3)]
    folders, results = [], []
    k = 0
    for nif, name in buyers:
        own = nif in ("Q0000001A", "Q0000002B")
        n_folders = 220 if own else 130
        for _ in range(n_folders):
            k += 1
            cpv, stem, budget = CATEGORIES[rng.integers(len(CATEGORIES))]
            year = int(rng.integers(years[0], years[1] + 1))
            notice = date(year, 1, 1) + timedelta(days=int(rng.integers(0, 300)))
            if notice > AS_OF - timedelta(days=60):
                notice = AS_OF - timedelta(days=int(rng.integers(60, 400)))
            lead = int(rng.integers(45, 160))
            award = notice + timedelta(days=lead)
            months = int(rng.choice([12, 24, 36, 48]))
            bud = float(round(budget * rng.lognormal(0, 0.5), 2))
            open_proc = rng.random() < 0.85
            n_bids = int(min(9, rng.geometric(0.42 if not own else 0.5)))
            discount = float(np.clip(0.02 * (n_bids - 1) ** 0.9 + rng.normal(0.03, 0.03), 0, 0.6)) if n_bids > 1 \
                else float(np.clip(rng.normal(0.005, 0.01), 0, 0.1))
            entry = f"demo-{k}"
            updated = (award + timedelta(days=5)).isoformat() + "T00:00:00"
            fid = f"{k:05d}/{year}"
            folders.append({
                "entry_id": entry, "updated": updated, "link": f"https://example.invalid/{entry}",
                "title": f"{stem} (expediente {fid})", "folder_id": fid, "status": "RES", "buyer": name,
                "buyer_nif": nif, "buyer_dir3": None, "buyer_city": "Ciudad de Ejemplo",
                "buyer_parents": "Servicio de Salud de Ejemplo > Comunidad de Ejemplo", "buyer_type": "3",
                "is_health": True, "contract_type": "2" if cpv.startswith(("5", "8", "9")) else "1",
                "contract_subtype": None, "cpvs": cpv, "budget_estimated": bud * 1.2, "budget_total": bud * 1.21,
                "budget_net": bud, "nuts": "ES99X", "planned_start": None, "planned_end": None,
                "duration": float(months), "duration_unit": "MON", "extension_desc": None,
                "extension_period": "1 año" if rng.random() < 0.4 else None, "n_lots": 0, "lots_json": None,
                "procedure": "1" if open_proc else "6", "urgency": "1", "contracting_system": "0",
                "over_threshold": "false", "submission_deadline": (notice + timedelta(days=30)).isoformat(),
                "funding": None, "attachments_json": "[]", "notice_cn": notice.isoformat(), "notice_pin": None,
                "notice_award": award.isoformat(), "notice_formal": None, "notices": "{}", "source_feed": "643",
                "source": "demo"})
            winner = int(rng.integers(len(SUPPLIERS)))
            results.append({
                "entry_id": entry, "updated": updated, "folder_id": fid, "buyer_nif": nif, "source_feed": "643",
                "lot_id": None, "result_code": "8", "result_desc": "Adjudicado", "award_date": award.isoformat(),
                "n_bids": float(n_bids), "n_sme_bids": None, "lower_bid": None, "higher_bid": None,
                "winner": SUPPLIERS[winner], "winner_nif": _nif("B", winner + 1), "sme_awarded": "true",
                "award_net": round(bud * (1 - discount), 2), "contract_id": None,
                "contract_date": (award + timedelta(days=20)).isoformat(), "contract_start": None})
    # Contracts that end soon with no successor yet: what the radar exists to catch. Distinct titles so the
    # successor matcher (title trigrams) cannot pair them with the recurring purchases above.
    expiring = ["Mantenimiento de los ascensores", "Suministro de lentes intraoculares",
                "Servicio de esterilización externa", "Mantenimiento de la resonancia magnética",
                "Suministro de prótesis de cadera", "Servicio de transporte sanitario no urgente"]
    for b, (nif, name) in enumerate(buyers[:2]):
        for j, stem in enumerate(expiring):
            k += 1
            months = 24
            ends_in = int(rng.integers(20, 240))
            award = AS_OF + timedelta(days=ends_in) - timedelta(days=30 * months + 30)
            notice = award - timedelta(days=90)
            cpv = CATEGORIES[(j + b) % len(CATEGORIES)][0]
            bud = float(round(150_000 * rng.lognormal(0, 0.4), 2))
            n_bids = 1 if j % 2 == 0 else 2
            entry, fid = f"demo-x{k}", f"X{k:04d}/{award.year}"
            updated = (award + timedelta(days=5)).isoformat() + "T00:00:00"
            folders.append({**folders[0], "entry_id": entry, "updated": updated, "link": f"https://example.invalid/{entry}",
                            "title": f"{stem} {name.split()[-1]}", "folder_id": fid, "buyer": name, "buyer_nif": nif,
                            "cpvs": cpv, "budget_net": bud, "budget_estimated": bud * 1.2, "budget_total": bud * 1.21,
                            "duration": float(months), "procedure": "1", "extension_period": None,
                            "notice_cn": notice.isoformat(), "notice_award": award.isoformat(),
                            "submission_deadline": (notice + timedelta(days=30)).isoformat()})
            results.append({**results[0], "entry_id": entry, "updated": updated, "folder_id": fid, "buyer_nif": nif,
                            "award_date": award.isoformat(), "n_bids": float(n_bids),
                            "winner": SUPPLIERS[j], "winner_nif": _nif("B", j + 1),
                            "award_net": round(bud * (0.995 if n_bids == 1 else 0.93), 2),
                            "contract_date": (award + timedelta(days=20)).isoformat()})
    return pd.DataFrame(folders), pd.DataFrame(results)


def build() -> Path:
    """Generate, build the spine with the production code, write fact sheets. Idempotent.

    Must run in a process started with DEMO_ENV (paths are resolved at import time); `main` ensures that.
    """
    from licita import config
    from licita.memo import facts
    from licita.profile import load_profile
    from licita.spine import build as spine_build
    folders, results = generate()
    config.PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    folders.to_parquet(config.PARQUET_DIR / "demo_folders.parquet", index=False)
    results.to_parquet(config.PARQUET_DIR / "demo_results.parquet", index=False)
    spine = spine_build.build(AS_OF)
    for buyer in load_profile().buyers:
        facts.build_all(buyer, ("start_now", "due_6m"), spine=config.SPINES_DIR / "spine.duckdb",
                        out_dir=config.OUT_DIR)
    DEMO_DIR.mkdir(parents=True, exist_ok=True)       # data paths may point elsewhere (tests, LICITA_DATA)
    (DEMO_DIR / "README.md").write_text(
        "Generated by `python -m licita.demo build`: synthetic data, safe to delete.\n")
    print(f"demo spine -> {spine}\nfact sheets -> {config.OUT_DIR}")
    return spine


def ready() -> bool:
    return (DEMO_DIR / "spines" / "spine.duckdb").exists() and (DEMO_DIR / "out").exists()


def app(port: str | None = None) -> int:
    env = {**os.environ, **DEMO_ENV}
    if not ready():
        subprocess.run([sys.executable, "-m", "licita.demo", "build"], env=env, check=True)
    cmd = [sys.executable, "-m", "streamlit", "run", str(ROOT / "src" / "licita" / "app" / "main.py")]
    if port:
        cmd += ["--server.port", port]
    return subprocess.call(cmd, env=env)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["build"]:
        if os.environ.get("LICITA_DEMO") != "1":      # re-run with demo paths so nothing touches real data
            return subprocess.call([sys.executable, "-m", "licita.demo", "build"], env={**os.environ, **DEMO_ENV})
        build()
        return 0
    if args[:1] == ["app"]:
        return app(args[1] if len(args) > 1 else None)
    if args[:1] == ["sample"]:   # print a few generated rows (debugging the generator)
        f, r = generate()
        print(json.dumps({"folders": len(f), "results": len(r)}))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
