"""Datasheet for the OCDS release, after Gebru et al., "Datasheets for Datasets" (2021).

Filled from the spine's `meta` and table counts, the build's data-quality report when present, and the
known-issues table maintained in docs/engineering/DATA.md (read at run time so the two never drift).
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import duckdb

from licita.config import ROOT

DATA_MD = ROOT / "docs" / "engineering" / "DATA.md"
KNOWN_ISSUES_HEADING = "## Known data-quality issues and their handling"


def known_issues(path: Path = DATA_MD) -> str:
    """The known-issues table from DATA.md, or a pointer if the file is not there."""
    if not path.is_file():
        return f"See `{path.name}` in the source repository."
    text = path.read_text(encoding="utf-8")
    m = re.search(re.escape(KNOWN_ISSUES_HEADING) + r"\n(.*?)(?=\n## |\Z)", text, re.S)
    return m.group(1).strip() if m else f"See `{path.name}` in the source repository."


def _dq_table(dq_report_path: Path | None) -> str:
    if dq_report_path is None or not dq_report_path.is_file():
        return "No data-quality report was found for this build."
    lines = [ln for ln in dq_report_path.read_text(encoding="utf-8").splitlines() if ln.startswith("|")]
    return "\n".join(lines) if lines else "The data-quality report holds no table."


def _counts(con: duckdb.DuckDBPyConnection) -> dict:
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    return {
        "folders": q("select count(*) from folders"),
        "folders_health": q("select count(*) from folders where is_health"),
        "awards": q("select count(*) from lots"),
        "buyers": q("select count(distinct buyer_key) from folders"),
        "suppliers": q("select count(*) from suppliers"),
        "suppliers_persons": q("select count(*) from suppliers where is_person"),
        "feed_643": q("select count(*) from folders where source_feed = '643'"),
        "feed_1044": q("select count(*) from folders where source_feed = '1044'"),
        "first_award": q("select min(award_d) from lots where award_d >= date '2000-01-01'"),
        "last_award": q("select max(award_d) from lots where award_d <= current_date + interval 400 day"),
    }


def datasheet(con: duckdb.DuckDBPyConnection, counts: dict, dq_report_path: Path | None = None) -> str:
    run_id, as_of, through, schema = con.execute("select run_id, as_of, data_through, schema_version from meta").fetchone()
    t = _counts(con)
    c = counts
    return f"""# Datasheet: Licita health procurement, OCDS 1.1 release

Generated {date.today().isoformat()} from spine `{run_id}` (schema {schema}), data through **{through}**,
radar as of {as_of}. Format: OCDS 1.1 compiled releases, JSON Lines, UTF-8, gzip.

## Motivation

Built so that a hospital procurement team can see, before drafting a tender, which contracts are about to
lapse, how comparable buyers tendered the same object, and whether a single bid is structural or avoidable.
Independent, non-profit research on open data; no funding from buyers or suppliers. The release exists so that
anyone can re-run the figures and so that other teams can reuse the cleaned slice without repeating the
10 GB download and the join work.

## Composition

- One release per tender file (`ContractFolderStatus`) of a health-sector buyer: a buyer whose name matches a
  health pattern, a tender with CPV 33*/85*, or a buyer in the deployment profile's region filter.
- This file: **{c.get('releases', 0):,} releases**, {c.get('awards', 0):,} active awards,
  {c.get('awards_unsuccessful', 0):,} unsuccessful (deserted) awards, {c.get('contracts', 0):,} contracts,
  {c.get('buyers', 0):,} buyers. Scope: since {c.get('since') or 'the first archive'}, buyers {c.get('buyers_scope', 'all-health')}.
- The spine it comes from: {t['folders']:,} tender files ({t['folders_health']:,} health), {t['awards']:,} awarded
  lots, {t['buyers']:,} buyers, {t['suppliers']:,} suppliers; feed 643 {t['feed_643']:,} files, feed 1044
  {t['feed_1044']:,}; awards dated {t['first_award']} to {t['last_award']}.
- Each release holds parties (buyer, suppliers), `tender` (procedure, category, budget, submission deadline,
  lots as items with CPV), `awards` (one per awarded or deserted lot) and `contracts` where PLACSP published
  a contract id or date. Fields the source did not publish are absent.
- **Personal data**: natural persons among awardees ({t['suppliers_persons']:,} supplier ids in the spine
  have a DNI/NIE shape) are excluded; {c.get('excluded_persons', 0):,} award rows were dropped from this file
  for that reason. Buyers are public bodies and suppliers are legal persons published by law.
- Identifiers: `ocid` = `{c.get('ocid_prefix', 'ocds-licita-')}` + SHA-1 of (buyer key, folder id). The prefix is
  **not registered** with the Open Contracting Partnership; register one before a public release.

## Collection process

Source: Plataforma de Contratación del Sector Público open data, CODICE/Atom archives, feed 643 (buyers
publishing on the platform) and feed 1044 (aggregated regional platforms), yearly archives 2017 onward and
monthly archives for the current year, closed months only. Fetched by `licita sync` (ETag change detection),
parsed by a streaming parser that keeps health buyers and the profile region's buyers. Every snapshot of a
tender is kept; the latest gives the tender's fields.

## Preprocessing and cleaning

- Snapshots loaded once (archives overlap); buyers resolved to one key (NIF is the identity; DIR3- and
  name-keyed buyers join their NIF under documented rules); awards unioned across snapshots, one version per
  (lot, winner) from the newest snapshot that mentions it; NIFs upper-cased; feed 1044 award dates proxied
  from contract or award-notice dates (`award_date_source` in the spine).
- Discounts in the spine use the lot budget only when it is known; the OCDS file carries budgets and award
  values as published and leaves ratios to the reader.
- Quality gate of the build that produced this file:

{_dq_table(dq_report_path)}

- Known issues and their handling (from the engineering data notes):

{known_issues()}

## Uses

Intended: benchmarking competition and timeliness across buyers within lot mix and procedure; market
consultation invite lists; research on single bidding, extensions and deserted lots. Not intended: ranking
named buyers without the coverage and mix adjustments described in the data notes; inferring wrongdoing from
a single bid (exclusivity is a lawful route); any use of supplier data about natural persons (none is
included).

## Distribution

Derived from PLACSP open data. The source's legal notice governs reuse of the underlying records; this
derived dataset is offered under CC BY 4.0 unless the source terms forbid it (**to confirm before release**).
Cite as: Licita, OCDS release of Spanish health procurement, spine {run_id}, data through {through}.

## Maintenance

Rebuilt by `licita sync` whenever PLACSP closes a month; each build is versioned (`run_id`), gated and
reported. Corrections: open an issue in the source repository with the `ocid` and the field. The known-issues
table above is the changelog of data defects found so far.
"""
