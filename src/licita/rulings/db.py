"""The rulings database: one schema, one connection function, versioned migrations.

Ruling ids are source-qualified (`<source>:<num>/<year>`), so a second tribunal can never overwrite the first.
The source key is a slug of the profile's tribunal short name (`tribunal` when unset).

Schema versions
  1  unqualified ids ("12/2024"), extraction rows without provider/prompt metadata (pre-Mistral)
  2  source-qualified ids, `rulings.source`, `ruling_texts.text_source`, extraction provenance columns,
     batch job state and per-request membership (`batch_requests`)
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb

from licita.config import STATE_DIR

DB = STATE_DIR / "rulings.duckdb"
SCHEMA_VERSION = 2

DDL = {
    "rulings": """create table if not exists rulings (
        id varchar primary key, source varchar, year int, num int, url varchar, status varchar, error varchar,
        fecha varchar, organo varchar, acto varchar, objeto varchar, tipo_contrato varchar,
        tipo_resolucion varchar, summary varchar, pdf_url varchar, fetched_at timestamp)""",
    "ruling_texts": """create table if not exists ruling_texts (
        id varchar primary key, status varchar, error varchar, pages int, chars int, text varchar,
        fetched_at timestamp, text_source varchar default 'pdfplumber')""",
    "extraction_batches": """create table if not exists extraction_batches (
        batch_id varchar primary key, model varchar, n int, scope varchar, submitted_at timestamp,
        collected_at timestamp, provider varchar, prompt_hash varchar, state varchar, n_succeeded int,
        n_failed int, n_missing int)""",
    "batch_requests": """create table if not exists batch_requests (
        batch_id varchar, id varchar, primary key (batch_id, id))""",
    "ruling_extractions": """create table if not exists ruling_extractions (
        id varchar, model varchar, run_id varchar, status varchar, error varchar, data json,
        input_tokens int, output_tokens int, collected_at timestamp, provider varchar, prompt_hash varchar,
        text_source varchar, finish_reason varchar, latency_s double, attempts int, batch boolean,
        primary key (id, model))""",
    "ruling_grounds": """create table if not exists ruling_grounds (
        id varchar, model varchar, idx int, ground varchar, document varchar, quote varchar,
        quote_verified boolean, quote_score double, articles json, holding varchar, summary varchar,
        primary key (id, model, idx))""",
}
ID_TABLES = ("rulings", "ruling_texts", "ruling_extractions", "ruling_grounds", "batch_requests")
ID_RE = re.compile(r"^(?:(?P<source>[a-z0-9_-]+):)?(?P<num>\d+)/(?P<year>\d{4})$")


def source_key() -> str:
    from licita.profile import load_profile
    slug = re.sub(r"[^a-z0-9]+", "-", load_profile().tribunal.short.lower()).strip("-")
    return slug or "tribunal"


def ruling_id(num: int, year: int, source: str | None = None) -> str:
    return f"{source or source_key()}:{num}/{year}"


def split_id(rid: str) -> tuple[str | None, int, int]:
    m = ID_RE.match(rid)
    if not m:
        raise ValueError(f"not a ruling id: {rid!r}")
    return m["source"], int(m["num"]), int(m["year"])


def _columns(con, table: str) -> list[str]:
    return [r[0] for r in con.execute(f"select column_name from information_schema.columns "
                                      f"where table_name = '{table}' order by ordinal_position").fetchall()]


def _version(con) -> int:
    con.execute("create table if not exists schema_meta (key varchar primary key, value varchar)")
    row = con.execute("select value from schema_meta where key = 'version'").fetchone()
    if row:
        return int(row[0])
    has_old = con.execute("select count(*) from information_schema.tables where table_name = 'rulings'").fetchone()[0]
    return 1 if has_old else 0


def _rebuild(con, table: str, select_sql: str) -> None:
    """Recreate a table under the current DDL (DuckDB cannot update indexed key columns in place)."""
    tmp = f"{table}__v{SCHEMA_VERSION}"
    con.execute(f"drop table if exists {tmp}")
    con.execute(DDL[table].replace(f"exists {table} (", f"exists {tmp} (", 1))
    new_cols = _columns(con, tmp)
    con.execute(f"insert into {tmp} ({', '.join(new_cols)}) {select_sql.format(cols=', '.join(new_cols))}")
    con.execute(f"drop table {table}")
    con.execute(f"alter table {tmp} rename to {table}")


def migrate_1_to_2(con, source: str) -> None:
    for table in ID_TABLES:
        if not con.execute(f"select count(*) from information_schema.tables where table_name = '{table}'").fetchone()[0]:
            continue
        old = set(_columns(con, table))
        new = []
        con.execute(DDL[table].replace(f"exists {table} (", f"exists {table}__probe (", 1))
        for c in _columns(con, f"{table}__probe"):
            if c == "id":
                new.append(f"case when id like '%:%' then id else '{source}:' || id end as id")
            elif c == "source" and table == "rulings":
                new.append(f"'{source}' as source")
            elif c == "text_source" and table == "ruling_texts" and c not in old:
                new.append("'pdfplumber' as text_source")
            else:
                new.append(c if c in old else f"null as {c}")
        con.execute(f"drop table {table}__probe")
        _rebuild(con, table, f"select {', '.join(new)} from {table}")
    if con.execute("select count(*) from information_schema.tables where table_name = 'extraction_batches'").fetchone()[0]:
        old = set(_columns(con, "extraction_batches"))
        con.execute(DDL["extraction_batches"].replace("exists extraction_batches (", "exists eb__probe (", 1))
        cols = [c if c in old else f"null as {c}" for c in _columns(con, "eb__probe")]
        con.execute("drop table eb__probe")
        _rebuild(con, "extraction_batches", f"select {', '.join(cols)} from extraction_batches")


def connect(path: Path | str = DB, source: str | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the rulings database, creating or migrating it to SCHEMA_VERSION."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if read_only:
        return con
    version = _version(con)
    con.execute("begin transaction")   # a failed migration must leave the v1 tables untouched
    try:
        if version == 1:
            migrate_1_to_2(con, source or source_key())
        for ddl in DDL.values():
            con.execute(ddl)
        con.execute("insert or replace into schema_meta values ('version', ?)", [str(SCHEMA_VERSION)])
        con.execute("commit")
    except BaseException:
        con.execute("rollback")
        raise
    return con
