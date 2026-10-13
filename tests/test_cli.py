import duckdb
import pytest

from licita import cli
from licita.app import data


def test_usage_lists_every_command(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert all(f"  {name}" in out for name in cli.COMMANDS) and "licita demo app" in out


def test_unknown_command_exits_2(capsys):
    assert cli.main(["nope"]) == 2


def test_config_never_prints_a_key(monkeypatch, capsys):
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-very-secret")
    monkeypatch.setenv("LICITA_MODEL_EXTRACT", "mistral-medium-latest")
    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert "sk-very-secret" not in out and "MISTRAL_API_KEY set" in out
    assert "extract  mistral-medium-latest  [env]" in out and "chat     mistral-large-latest  [default]" in out


def test_extract_without_key_fails_with_the_fix(capsys, tmp_path, monkeypatch):
    from licita.rulings import extract
    monkeypatch.setattr(extract, "DB", tmp_path / "r.duckdb")
    monkeypatch.setattr(extract, "_db", lambda path=None: extract.connect(tmp_path / "r.duckdb"))
    assert cli.main(["extract", "submit", "--limit", "1"]) == 2
    out = capsys.readouterr().out
    assert "MISTRAL_API_KEY is not set" in out and "make demo" in out


def test_spine_checks(tmp_path):
    with pytest.raises(Exception, match="no spine"):
        data.check_spine(tmp_path / "missing.duckdb")
    old = tmp_path / "old.duckdb"
    with duckdb.connect(str(old)) as c:
        c.execute("create table meta as select 'r' as run_id")
    with pytest.raises(Exception, match="spine schema 1 is older"):   # pre-versioning spines count as schema 1
        data.check_spine(old)
    new = tmp_path / "new.duckdb"
    with duckdb.connect(str(new)) as c:
        c.execute("create table meta as select 'r' as run_id, 0 as schema_version")
    with pytest.raises(Exception, match="rebuild required"):
        data.check_spine(new)
    current = tmp_path / "current.duckdb"
    with duckdb.connect(str(current)) as c:
        c.execute("create table meta as select 'r' as run_id, ? as schema_version", [data.SPINE_SCHEMA])
    assert data.check_spine(current) == data.SPINE_SCHEMA
