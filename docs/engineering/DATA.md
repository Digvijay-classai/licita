# Data

## Sources

| Source | What | Volume | Access |
|---|---|---|---|
| PLACSP feed 643 | Tenders published directly on the national platform | yearly zips 2017–2025, monthly 2026 | open data, CODICE/Atom |
| PLACSP feed 1044 | Tenders aggregated from regional platforms | same | open data |
| Regional tribunal | Resolutions of the profile's procurement tribunal | every ruling published online (from the profile's `first_year`), with text | public website described by the profile, polite scraper (1 s delay) |

36 archives, about 10 GB compressed, reduced to 674 MB of Parquet (health buyers and the
profile region's buyers only) and a spine of about 450,000 awarded lots. The spine holds **closed months
only**: a month enters once PLACSP has closed its archive, so every figure has a clean cut-off
(`meta.data_through`, shown on every page). The month in progress is opt-in (`licita sync --current-month`).

## Refresh connector (`licita sync`)

`licita sync` keeps the local mirror current without anyone remembering to run it:

1. Plans the archives that should exist: yearly up to last year, monthly for this year up to last month.
2. Asks the server for each archive's ETag (`<size>-<mtime>`), one streaming GET closed after the headers.
3. Fetches and parses only archives whose ETag changed or whose Parquet is missing. Parquet is written to a
   temporary name and renamed, so a build never reads a half-written file. `data/state/archives.json`
   records ETag, size, time, row counts and the parse signature (parser version + the profile's parse-time
   filters); a changed signature re-parses everything (`--reparse` forces it).
4. If anything changed, or there is no live spine, builds a new spine. The build runs the quality gate
   below before the symlink swap; a failing gate leaves the previous spine live and writes the report.
5. With `--facts`, regenerates the profile buyers' fact sheets.

A lock file makes overlapping runs harmless; each run appends a line to `data/state/sync_log.jsonl`.
`make schedule-install` registers it with launchd (daily, `HOUR` and `PROFILE` configurable, missed slots
run at the next wake); `ops/cron.example` is the Linux equivalent. `make schedule-status` shows the last runs.

## Definitions (single source: `src/licita/spine/views.sql`)

- **Snapshot**: one Atom entry of a tender at one `updated` time. The same snapshot sits in overlapping
  daily files and in two archives; it is loaded once.
- **Buyer key** (`buyer_xref`, rules in `spine/buyers.py`): a NIF is the identity and two NIFs never merge.
  A DIR3-only key joins the NIF it was ever published next to, when that is exactly one NIF. A name-only key
  joins an identified key when name, city and first parent match exactly one, or (names of 20+ characters)
  when name and city do. Everything else keeps its raw key (NIF, else `DIR3:`, else normalised name and
  parent chain). Feed 1044 never carries a NIF and feed 643 lacked it in 2017.
- **Latest snapshot**: the latest per (buyer key, folder id) gives the folder's fields; `n_snapshots` counts
  the versions seen.
- **Awards**: the union over a folder's snapshots, one row per (lot, winner, contract id), latest version
  wins. Large frameworks publish awards in pages across snapshots; the latest snapshot alone held 4.2 % fewer
  award rows (19,325 of 462,019 on the September 2026 build). `from_latest_snapshot` marks rows the latest snapshot still carries. Non-award rows (deserted,
  withdrawn) and every row of an annulled folder come from the latest snapshot only.
- **Awarded lot**: result codes 2, 8, 9 and 11 (awarded, finally awarded, formalised).
- **Award date** (`award_d`, `award_date_source`): the result's award date; feed 1044 publishes none, so the
  contract date, then the folder's award-notice date, stand in. Time-based analyses can restrict on the source.
- **Open procedure**: procedure codes 1 and 9 (open and simplified open).
- **Discount**: `1 − award / lot budget`, kept only when award / budget is between 0.05 and 1.05
  (a discount from −5 % to 95 %); anything else is a data error.
- **Lot budget**: from the folder's lot list; a lot inherits the folder budget only when it is the folder's
  only awarded result.
- **Value band**: lot budget < €15k, < €100k, < €500k, ≥ €500k.
- **Supplier** (`suppliers`): the winner's NIF (upper-cased, trimmed) is the identity; the most frequent
  spelling is the display name; `is_person` flags natural persons (DNI/NIE shape), who never appear in lists.
- **Contract end**: `end_initial` (start + duration) drives "start by"; `end_max` (with extensions)
  is shown for information, because extensions are not always exercised.
- **Lead time**: p80 of call-for-tenders → award days by CPV division, open procedures.

## Derived tables (schema 3)

| Table | One row per | Definition |
|---|---|---|
| `reasons` | single-bid open lot | bucket ∈ {structural, earlier_decision, addressable}; structural when the manufacturer is the incumbent on a brand-named contract, the peer cell is ≥ 80 % single bid (n ≥ 20) or the title carries an exclusivity cue; earlier_decision for extensions, framework call-offs and compatibility cues; else addressable with its sub-signals (thin market, short deadline, large lot, services). Confidence 0.9 two signals, 0.7 one, 0.5 none. `signals` lists what fired; `lever` is the next-tender action |
| `deserted` | result with code 3, 4 or 5 | budget (lot list or single-lot folder), deserted date (award, contract, award notice, snapshot), republication within 12 months by the same buyer (title trigram Jaccard ≥ 0.5, or ≥ 0.25 with the same CPV-4), its outcome, and the national market for the CPV-4 (median bids, single-bid share) |
| `benchmark` | health buyer with ≥ 30 awarded lots since 2022 | timeliness on contracts ≥ 12 months that ended before as-of − 90 days: successor notice ≥ 90 days before the end (`on_time_share`), after the end (`late_share`), none (`no_successor_share`); `past_term_no_successor_eur_yr`; extension-as-award and negotiated shares; deserted rate; single bid actual vs expected from peer cells with a Wilson 95 % interval and empirical-Bayes shrinkage toward the region (k = 50); `value_at_risk_eur_yr` weighted by tier (1.0 / 0.6 / 0.2); coverage score (budget, real award date, notice date, duration, direct publication) and `comparable` (≥ 0.6 and ≥ 30 open lots); bands on_track / watch / behind as tertiles within the region among comparable buyers, data_limited otherwise. No rank column |
| `cpv_tiers` (shipped CSV) | CPV prefix | care-continuity tier: 1 stops care if it lapses, 2 degrades care or safety within weeks, 3 deferrable; longest-prefix match, unknown → 3 with low confidence |
| `truth`, `outcomes` | team answer / event | below |

The successor proxy in `benchmark` is coarser than the radar's matcher (same buyer and CPV-4, first notice after end − 365 days) and is labelled a proxy until the extension ground truth exists; the regional purchasing body therefore shows large "past term" values from frameworks that end without a same-CPV successor.

## What the team adds (ground truth and outcomes)

PLACSP never publishes exercised extensions, and the point of the pilot is a decision changed, so two
small CSV files outside git carry what only people know. `licita truth template extensions|outcomes`
writes the templates; files live in `data/truth/` (one extensions file per buyer plus `outcomes.csv`).

- **Extensions** (`truth` table): per (buyer key, folder id) a status in {extended, extension_pending,
  replaced, replaced_by_framework, in_progress, lapsed, ended_no_need}, optional successor folder and
  `extended_until`, the source and the date recorded. The radar applies it over the heuristic successor
  match (`truth_status`, `heuristic_status` columns), and the build report prints the matcher's precision
  and recall against it (`matcher_precision`).
- **Outcomes** (`outcomes` table): dated events per buyer and folder (memo delivered, file opened before the
  start-by date, market consultation run, decision changed, tender published, bids received, extension
  avoided), with € and bids where known. `licita truth outcomes` summarises them per buyer and month.

A malformed file fails the build with the offending line; `licita truth validate` checks before that.

## Open dataset release

`licita export --since 2022-01-01 --all` writes an OCDS 1.1 compiled-release JSONL (gzip) of the health
slice, a meta sidecar (spine run id, `data_through`, counts) and a datasheet (motivation, composition,
collection, cleaning, uses, distribution, maintenance; the DQ report and the known-issues table are
embedded). Natural-person suppliers are excluded and counted. The `ocds-licita-` prefix is a placeholder
until registered with the Open Contracting Partnership. Mapping choices (procedure code → OCDS method,
folder status → tender status, result codes → award status) are documented in `src/licita/export/ocds.py`.

## Gates every build must pass

Coverage floors on the gated slice (feed 643, 2018 onward): folder id and buyer key 100 %; buyer NIF 99 %;
CPV, procedure and budget 95 %; award date 95 %; bid count 85 %; result code 99 %.

Quality gate (`licita dq`, `runs/<run_id>/dq_report.md`), hard checks block the swap:

| Check | Level | Rule |
|---|---|---|
| folders_unique | hard | one row per (buyer key, folder id) |
| results_no_exact_duplicates | hard | no award row repeated verbatim |
| awards_retained | hard | the union never holds fewer awards than the latest snapshot alone |
| award_key_unique | hard | one row per (buyer, folder, lot, winner, contract id) |
| award_single_version | hard | a (lot, winner) pair comes from one snapshot only |
| award_date_present | hard | ≤ 5 % of awarded lots without any date |
| award_date_plausible | hard | ≤ 0.5 % of dated lots outside 2000 … as-of + 400 days |
| months_continuous | hard (soft on demo data) | no empty month between the first and last archive, per feed |
| single_bid_median_discount | hard (soft on demo data) | median discount with one bid ≤ 2 % |
| buyer_nif_format, buyer_keys_resolved, months_no_dip, data_through, single_bid_rate_plausible, feed_1044_lot_budgets | soft | reported, never block |

Thresholds were set on the 2017–2026 archives on 2026-10-10. Do not lower one to pass; find the cause.

## Completeness audit (2026-10-10, archives through September 2026)

- All 36 archives present; every September archive on the server is byte-identical (ETag) to the local copy,
  so September 2026 is closed and complete. October's archives exist on the server but are partial and are
  not loaded.
- Every month from 2017-01 to 2026-09 has snapshots in both feeds; the newest snapshot is 2026-09-30 (feed
  643) and the first minutes of 2026-10-01 (feed 1044). Weekend days carry a handful of entries, as expected.
- Feed 643 doubles its monthly volume in October 2022 (new folders per month: ~1,300 → ~3,300). It is more
  publication by existing buyers, not new buyers (buyers first seen after that date are 7.6 % of 2023 rows).
  Year-on-year comparisons across 2022 must allow for it.
- A monthly archive contains daily files whose dates overlap the previous month; the loader's snapshot
  dedupe absorbs that.

## Known data-quality issues and their handling

| Issue | Handling |
|---|---|
| Awards paged across snapshots (mostly frameworks): 19,325 award rows (4.2 %) exist only in an earlier snapshot | One version per (lot, winner) from the newest snapshot that mentions it; an award republished as a formalisation replaces its earlier row (the first attempt keyed on contract id too and double-counted 142,542 rows; `award_single_version` now guards it) |
| One buyer under several keys: 567 name/DIR3 keys (4,405 folders) belonged to an identified buyer, and 5,247 tenders sat under two keys at once | `buyer_xref` resolves keys (rules above); `buyer_keys_resolved` reports what remains |
| Feed 1044 publishes no award date on results (230,863 rows) | Contract date, then award-notice date; `award_date_source` records which |
| Buyer NIFs with lower case or blanks (61 folders); winner NIFs likewise | Upper-cased and trimmed at build time; residual malformed NIFs reported |
| One supplier under hundreds of spellings (one NIF had 270) | `suppliers` keyed by NIF with the modal spelling; `winner_nif` normalised in `results` |
| Lots with 8+ bids show a 62.6 % median "discount" | Unit-price tenders and framework call-offs; excluded from the curve (k ≤ 7) |
| Folders with several awarded results but no lot list showed a ~76 % median "discount" at every bid count (40,113 lots in feed 1044, 4,615 in feed 643) | Each lot was compared with the whole folder budget. A lot now inherits the folder budget only when it is the folder's only awarded result; otherwise its discount is null and it still counts for bid statistics. `single_bid_median_discount` guards it |
| Award rows repeated across overlapping archives (3.6 % of results) and 953 near-repeats | Snapshots are loaded once and awards are keyed, so both vanish by construction; `award_key_unique` guards it |
| A raw single-bid gap between a hospital and its peers was mostly lot mix | Compared within CPV-4 × value band, never raw |
| One-off purchases (works, contracts under 6 months) flooded the radar | Flagged `one_off` and excluded from re-tender alerts |
| "Desestimación" contains "estima" | Outcome classifier matches prefixes, with tests |
| CPV codes too generic for peers | Topic peers on title words, scope shown in the memo |
| Some award dates are malformed (e.g. year 0010) | Ignored by date-bounded queries; counted by `award_date_plausible` |
| Titles with runs of blanks (19,590) and a few mojibake strings | `title_clean` for display and matching; raw title kept |
| Some public hosts omit a TLS intermediate certificate | The deployer adds the public intermediate under `data/state/certs/` (or `LICITA_CA_EXTRA`); verification is never disabled |

## What is not in git

All downloaded data, the spines, run reports, fact sheets, hospital-specific outputs and tender
documents are git-ignored. The repository contains code, tests and documentation only.
