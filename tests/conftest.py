import socket
from pathlib import Path

import pytest

from licita import profile

TEST_PROFILE = Path(__file__).parent / "fixtures" / "profiles" / "test.toml"


@pytest.fixture(autouse=True)
def synthetic_profile(monkeypatch):
    """Every test runs against an invented region and buyers, never a real deployment profile."""
    monkeypatch.setenv("LICITA_PROFILE", str(TEST_PROFILE))
    profile._load.cache_clear()
    yield
    profile._load.cache_clear()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No network and no real credentials in tests: HTTP goes through httpx.MockTransport only."""
    def refuse(*args, **kwargs):
        raise RuntimeError("network access is disabled in tests; use httpx.MockTransport")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for var in ("MISTRAL_API_KEY", "LICITA_LLM_PROVIDER", "LICITA_LLM_BASE_URL", "LICITA_LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
