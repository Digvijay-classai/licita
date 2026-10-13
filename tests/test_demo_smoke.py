"""The demo builds from synthetic data with the production pipeline, and every app page renders.

Runs in subprocesses because data paths are resolved from the environment at import time.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from licita.config import ROOT

SMOKE = """
from streamlit.testing.v1 import AppTest
at = AppTest.from_file({main!r}, default_timeout=120).run()
assert not at.exception, at.exception
texts = " ".join(str(m.value) for m in at.get("html"))
assert "invented hospitals" in texts or "inventados" in texts, "demo banner missing"
for page in ("nav.radar", "nav.region", "nav.memos", "nav.simulator", "nav.chat"):
    at.session_state["page"] = page
    at.run()
    assert not at.exception, (page, at.exception)
at.session_state["lang"] = "en"
at.run()
assert not at.exception
print("SMOKE_OK")
"""


@pytest.fixture(scope="module")
def demo_env(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    env = {**os.environ, "LICITA_PROFILE": "example", "LICITA_DATA": str(d / "data"), "LICITA_SPINES": str(d / "spines"),
           "LICITA_RUNS": str(d / "runs"), "LICITA_OUT": str(d / "out"), "LICITA_DEMO": "1"}
    env.pop("MISTRAL_API_KEY", None)
    subprocess.run([sys.executable, "-m", "licita.demo", "build"], env=env, check=True, capture_output=True, timeout=300)
    return env, d


def test_demo_build_produces_spine_radar_and_fact_sheets(demo_env):
    _, d = demo_env
    assert (d / "spines" / "spine.duckdb").exists()
    sheets = list((d / "out").glob("*/facts/*.json"))
    assert len(sheets) >= 10


def test_every_app_page_renders_on_demo_data(demo_env):
    env, _ = demo_env
    main = str(Path(ROOT) / "src" / "licita" / "app" / "main.py")
    out = subprocess.run([sys.executable, "-c", SMOKE.format(main=main)], env=env, capture_output=True, text=True,
                         timeout=300)
    assert "SMOKE_OK" in out.stdout, out.stderr[-3000:]
