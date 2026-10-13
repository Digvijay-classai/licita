"""Shared HTTP client with full TLS verification.

Some public servers misconfigure TLS, typically by not sending an intermediate certificate. We never
disable verification. Instead the deployer drops the missing public intermediate (fetched from the leaf
certificate's AIA URL and checked against Mozilla's roots) into `data/state/certs/` or points
`LICITA_CA_EXTRA` at a PEM file or folder, and it is appended to certifi's bundle. Nothing host-specific
ships with the code.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import certifi
import httpx

from licita.config import STATE_DIR

CERTS_DIR = STATE_DIR / "certs"
USER_AGENT = "licita/0.1 (open-data research; contact in project README)"


@lru_cache(maxsize=1)
def ca_bundle() -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = STATE_DIR / "ca_bundle.pem"
    extra = sorted(CERTS_DIR.glob("*.pem")) if CERTS_DIR.is_dir() else []
    env = os.environ.get("LICITA_CA_EXTRA", "").strip()
    if env:
        ep = Path(env)
        extra += sorted(ep.glob("*.pem")) if ep.is_dir() else [ep]
    parts = [Path(certifi.where()).read_text()] + [p.read_text() for p in extra if p.is_file()]
    out.write_text("\n".join(parts))
    return str(out)


def client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(
        verify=ca_bundle(),
        timeout=httpx.Timeout(timeout, read=300.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
