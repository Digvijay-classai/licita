# TODOS

## Deferred from the design review (2026-10-08)
- [ ] **Draft-pliego upload linting.** The team uploads an unpublished draft and gets clause flags before
      publication. Blocked on: revisiting P2 (public data only), DPO/IT sign-off, zero-retention API
      configuration, a written governance note, and an LCSP art. 70 (prior involvement) review.


- [ ] **Model-based simulator v2** (from the review TODO). v1 builds it (user chose "build now"), and
      this tracks later calibration against real outcomes.

## Blocked / waiting (2026-10-09)
- [ ] **First Mistral run (needs MISTRAL_API_KEY; use PROFILE=local).** `make smoke-mistral` (one call) → `make label` → label 30-50 rulings (guide in
      docs/engineering/EVALS.md) → `make evals` (Small, Medium, Large) → put the matrix in the README.
      Then `make extract && make collect` on all tender-document rulings with the chosen model.
- [ ] **Confirm prices** in `src/licita/llm/prices.toml` against mistral.ai/pricing; set `verified = true`.
- [ ] **Self-hosted comparison:** `docker compose --profile vllm up`, then
      `LICITA_LLM_PROVIDER=local make evals MODELS=mistralai/Mistral-Small-3.2-24B-Instruct-2506`.
- [ ] **Tender documents.** `python -m licita.memo.documents --buyer <KEY>` once the PLACSP document
      host is reachable again (connect timeouts since 2026-10-09).
- [x] **Fresh-history public repository** (2026-10-13): a single-commit import of the tree that passes
      `licita anon check`, audited by two independent reviews before publication. Development history
      stays private; keep publishing by re-importing, never by pushing the private history.

## v1.1 (from design doc)
- [ ] OCDS JSONL export + OCP Cardinal red-flag cross-check.
- [ ] Open dataset release (health PLACSP in OCDS + tribunal rulings corpus with labels) on
      Hugging Face/Zenodo, with a datasheet.
- [ ] Public national-aggregates deployment.
- [ ] Higgsfield pitch video / presentation visuals.

- [ ] **TACRC (national tribunal) health rulings in the eval corpus** (count-first gate decision C,
      2026-10-08). A single regional tribunal limits the golden set's health positives; TACRC covers
      INGESA and the regions that delegate to it. Predictable PDF URL pattern; no API.

## Showcase (outside the repo)
- [ ] 10-slide deck and a 5-minute video walkthrough (demo → evals → deployment options).
- [ ] One-page memo to a fictional regional health-service CIO (business case, rollout).
- [ ] Rehearse defending each design choice (two mock technical deep dives).

## Later
- [ ] **Suppliers in the app and invite lists.** `suppliers` (NIF identity, modal spelling) exists in the
      spine; the invite list and the memo still print the raw winner string. Switch them and show
      `n_spellings` on hover.
- [ ] **Benchmark successor proxy → radar matcher.** `benchmark.on_time_share` uses same-buyer same-CPV-4
      notices; the radar's trigram matcher is better. Run the matcher for every benchmarked buyer (≈ 5 s
      each) and replace the proxy; re-check the regional purchasing body's past-term value.
- [ ] **Reason buckets vs manual labels.** The rules and a manual pass over one buyer's single-bid lots
      disagree mainly on device-tied consumables (reagents locked to an analyser, implant systems), which
      the rules call addressable. Settle it in a labelling session with the team and encode the outcome
      (e.g. a CPV-5 "device-tied consumable" list).
- [ ] **OCDS lots extension** instead of lot budgets in `items[].unit.value`; register an ocid prefix before
      any public release.
- [ ] **Deserted tracker outcome at lot level** (currently folder level).
- [ ] **Awards that were withdrawn between snapshots.** The award union keeps an award a later snapshot
      dropped (correct for paged frameworks, wrong for a withdrawn award). Measure on folders whose latest
      status is RES with fewer than 5 results; add a rule if it is more than noise.
- [ ] Le Chat Enterprise connector (MCP server over the spine and extractions).
- [ ] Distil: fine-tune a small Mistral model on validated outputs of a larger one; compare cost.

## v2
- [ ] Losing-bidder identities from award-committee PDFs (actas) → a true "never bid here" invite list.
