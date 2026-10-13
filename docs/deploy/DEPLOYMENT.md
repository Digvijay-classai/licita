# Deployment

Three ways to run Licita for a hospital or a regional health service, from least to most sovereign. The
code is identical in all three; only configuration changes.

| | A. Hosted Mistral (EU SaaS API) | B. Customer cloud / VPC | C. Air-gapped on-premises |
|---|---|---|---|
| App + data | customer laptop or VM | customer VM or container platform | customer server |
| Model | Mistral La Plateforme | Mistral models in the customer's cloud tenancy, or vLLM on the customer's GPUs | vLLM on customer GPUs |
| `LICITA_LLM_PROVIDER` | `mistral` | `local` (OpenAI-compatible endpoint) or `mistral` with `LICITA_MISTRAL_URL` | `local` |
| Credential | `MISTRAL_API_KEY` | endpoint token, if any | none |
| Egress needed | Mistral API, PLACSP, tribunal site | PLACSP, tribunal site | none at run time (data and weights carried in) |
| OCR | Mistral OCR | Mistral OCR if available in the tenancy; else embedded PDF text | embedded PDF text (pdfplumber) |
| Batch API | yes (50% price) | if the endpoint offers it; else concurrent sync calls | concurrent sync calls |

## Capability matrix

| Feature | `mistral` | `local` (vLLM / Ollama) | Fallback when missing |
|---|---|---|---|
| Strict JSON-schema output | yes | yes (vLLM xgrammar; Ollama subset) | output is always validated after the call |
| Batch jobs | yes | no | sequential resumable job table, bounded concurrency |
| OCR | yes | no | pdfplumber text; `text_source` records which |
| Alias → dated model pinning | `/v1/models` | repository id is already pinned | — |

## Quick paths

```bash
# A: hosted
export MISTRAL_API_KEY=...            # from a secret manager in production
licita config                         # check provider, models, paths
make extract && make collect          # batch extraction of tender-document rulings

# C: self-hosted on one GPU box
docker compose --profile vllm up      # vLLM serving Mistral Small 3.2 + the app on :8502
make smoke-local                      # one extraction against LICITA_LLM_BASE_URL
```

The image (`Dockerfile`) runs as a non-root user and works with a read-only root filesystem; it starts the
demo by default. For a deployment, mount `profiles/<name>.toml`, `data/`, `spines/` and `out/`, and set
`LICITA_PROFILE`. Releases are signed with cosign and ship an SPDX SBOM:

```bash
cosign verify ghcr.io/<owner>/licita:<version> \
  --certificate-identity-regexp 'https://github.com/<owner>/licita/.github/workflows/release.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Sizing

| Component | Footprint |
|---|---|
| Full backfill | ~10 GB download, ~0.7 GB Parquet, spine ~5 GB (three kept for rollback) |
| Spine build | minutes on a laptop; atomic swap, so readers never see a half-built spine |
| Extraction | the tender-document rulings, one batch job per run; cost in the eval report |
| Self-hosted model | Mistral Small 3.2 24B: ~55 GB VRAM in bf16, less with a quantised build |

## Keeping the data current

`licita sync` is the connector: it compares the server's archive ETags with `data/state/archives.json`,
fetches only what changed, rebuilds the spine when something did (behind the quality gate) and refreshes
fact sheets. It loads closed months only, so the cut-off on every page is a month end; `--current-month`
adds the month in progress for a meeting.

| Where | How |
|---|---|
| macOS laptop | `make schedule-install PROFILE=mine HOUR=6` (launchd; a missed slot runs at the next wake). `make schedule-status`, `make schedule-uninstall` |
| Linux VM | the line in `ops/cron.example` |
| Container | run `licita sync --facts` as a sidecar on a schedule, sharing the `data/` and `spines/` volumes with the app |

Each run writes one line to `data/state/sync_log.jsonl` and, when it builds, `runs/<run_id>/build_report.md`
and `dq_report.md`. The app shows `meta.data_through`, the newest snapshot in the data, never the build date.
A full first load is still `make backfill build` (about 10 GB); `sync` then costs one request per archive
plus whatever changed.

## Upgrades

Semver tags; `CHANGELOG.md` lists changes. The spine records `schema_version`: after an upgrade that
changes it, the app refuses the old spine and says `make build`. The rulings database migrates itself
(schema v1 → v2 in place; back it up first: `cp data/state/rulings.duckdb data/state/rulings.bak.duckdb`).
