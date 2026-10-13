# Changelog

## [0.2.0] - unreleased

**Mistral-native, anonymous by construction, and runnable in a minute without a key.**

Licita now reads tribunal rulings with Mistral models, hosted or self-hosted on the same code path, and no
longer names any hospital, region or tribunal in its source: those live in a deployment profile. A new
reviewer can see the whole app on synthetic data with `make demo`.

### Fixed
- Discounts: a lot inherits the folder budget only when it is the folder's only awarded result. Folders
  without a lot list (all of feed 1044, some of 643) had produced a ~76 % "discount" at every bid count.
- Award rows repeated across overlapping archives (3.6 % of results) are dropped at build time.
- Headline figures restated on the corrected spine: median discount 0.0 % with one bid, 6.9 % with two,
  13.8 % with four (140,952 lots). The case-study value case uses the new curve.
- Awards published in pages across a tender's snapshots are no longer lost: the build keeps one version per
  (lot, winner) from the newest snapshot that mentions it. The latest-snapshot rule had dropped 4.2 % of
  award rows (19,325), almost all in framework agreements. Near-duplicate award rows vanish with the same key.
- One buyer under several keys (NIF in one snapshot, DIR3 or name in another) is resolved to one key
  (`buyer_xref`); 5,247 tenders had been counted twice.
- Feed 1044 lots now carry a date (contract date, then award-notice date) and enter peers and lead times.
- Buyer and winner NIFs are upper-cased and trimmed; titles are whitespace-normalised for matching.

### Added
- `licita sync`: the refresh connector. Detects changed PLACSP archives by ETag, fetches and parses only
  those, writes Parquet atomically, rebuilds the spine when something changed, regenerates fact sheets,
  logs every run. Closed months by default, the current month opt-in. `make schedule-install` runs it
  daily via launchd; `ops/cron.example` for Linux.
- `licita dq`: the data-quality gate every build must pass before the symlink swap (uniqueness, award
  retention, date sanity, month continuity, the single-bid median-discount regression), with a report
  per run.
- `suppliers` table (NIF identity, modal spelling, natural persons flagged); `meta.data_through`;
  `from_latest_snapshot` and `award_date_source` columns. Spine schema 2.
- Schema 3: `reasons` (why one bid, with signals and lever), `deserted` (deserted-lot tracker with
  republication and market), `benchmark` (per-buyer timeliness, competition vs expected with intervals,
  value at risk by care-continuity tier, coverage and bands), `cpv_tiers.csv`, `truth` and `outcomes`
  (the team's answers and the outcome log, `licita truth`), OCDS 1.1 export with datasheet
  (`licita export`). Memos lead with continuity; the app gains a Region page. The quality gate adds
  metric stability and lot-count drift against the live spine.
- Mistral provider: strict JSON-schema chat, Batch API (half price), OCR for scanned rulings, alias to
  dated model pinning. Local provider for vLLM/Ollama serving Mistral open weights; `docker compose
  --profile vllm` runs it on a GPU.
- Evaluation harness: golden-set scoring with Wilson intervals, cost per 1,000 rulings, latency,
  labeller agreement, tribunal-label check, offline replay; `licita label` samples rulings to label.
- Deployment profiles (`LICITA_PROFILE`) and an anonymity gate with a hashed denylist.
- `licita` CLI and `licita config`; `make help`, `make check`; demo mode on synthetic data.
- Container image (non-root, read-only friendly), CI image build with health check, signed releases
  with SBOM.
- Docs for evaluators: case study, deployment, security, governance, runbook, residual risk, ADRs.

### Changed
- Rulings database schema v2: source-qualified ids, provenance columns; v1 databases migrate in place.
- Batch collection is resumable and tolerant: invalid JSON, truncation, error lines, unknown or
  duplicate ids and unanswered requests become statuses; missing requests are re-queued.
- Spine records a schema version; the app explains a missing or outdated spine.

### Removed
- The previous vendor's client, credentials flow and dependencies.

## [0.1.0]
First build: PLACSP ingest, spine and coverage gates, radar, fact sheets, bilingual app, tribunal
scraper, open-model baseline.
