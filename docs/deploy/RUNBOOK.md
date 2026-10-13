# Runbook

Every error Licita raises prints a problem, a cause and a fix. This page lists the ones an operator meets.

| Symptom | Cause | Fix |
|---|---|---|
| `profile '…' not found` | `LICITA_PROFILE` points nowhere | set it to a file under `profiles/` or a `.toml` path; start from `profiles/example.toml` |
| `MISTRAL_API_KEY is not set` | live call without a key | export the key, or `LICITA_LLM_PROVIDER=local`, or `make demo` |
| first live call fails with HTTP 400/422 mentioning the schema | the API rejected part of the JSON schema | run `make smoke-mistral`; simplify the offending keyword in `SCHEMA` (`rulings/extract.py`) and rerun; the local `validate()` still guards outputs |
| `profile … does not match this rulings database` | scraping with a different profile than the one that built the DB | set `LICITA_PROFILE` to the original profile, or use a new `LICITA_DATA` folder |
| `Mistral rejected the API key (HTTP 401)` | revoked or wrong key | issue a new key in the console, export again |
| repeated `HTTP 429` in logs | rate limit | nothing: calls back off and retry (`attempts` column); lower `--limit` for big runs |
| batch `RUNNING` for hours | provider queue | `licita extract collect` again later; it is idempotent |
| collect reports `missing` | job ended without answering some requests | they are re-queued: run `licita extract submit` again |
| many `invalid_json` / `length` | model output problems | data, not an outage: compare models with `make evals`; raise max tokens only if `length` dominates |
| `cannot reach the local model server` | vLLM/Ollama down | `docker compose --profile vllm up`; check `curl $LICITA_LLM_BASE_URL/models` |
| app: `no spine at …` | data not built here | `make demo` or `make backfill build` |
| app: `spine schema … is older` | upgrade changed the spine | `make build` |
| build: `coverage below floor` | feed format changed or parsing broke | read `runs/<run>/build_report.md`; do not lower floors to pass |
| build: `quality gate failed, spine not swapped` | a hard check in `licita dq` failed; the previous spine is still live | read `runs/<run>/dq_report.md`; fix the cause (a bad archive: `licita sync --reparse`; a rule change: its test) and rebuild |
| `sync: another run holds the lock` | a scheduled and a manual run overlapped | nothing; the other run finishes and logs to `data/state/sync_log.jsonl` |
| sync reason `parse filter changed` on every archive | the profile's `buyer_chain_re` / `health_terms` or the parser version changed | expected: everything is re-parsed once (~10 GB download); `--dry-run` shows it first |
| sync fetched archives but `built` is null | a fetch failed, so the spine was kept | rerun `licita sync`; downloads resume per archive |
| build: `truth: … file:line` | a ground-truth CSV has a bad status or date | fix the row (`licita truth validate` lists them); statuses and columns are in the template |
| memo shows "Confirmed by the team" on a contract that is not | a stale row in `data/truth/<BUYER>_extensions.csv` | correct the row, `make build` (the radar reads the table at build time) |
| `anon: N finding(s)` in CI | a name entered the tree | move it into a profile; if it is a new name to protect, add it to `private/denylist.txt` and `licita anon hash private/denylist.txt > anon/denylist.sha256` |

## Routine

| When | What |
|---|---|
| Daily, unattended | `licita sync --facts` via `make schedule-install` (closed months → spine → fact sheets); `make schedule-status` to check |
| Monthly, before a meeting | `make refresh` (adds the month in progress → rulings → documents) |
| After any build | glance at `runs/<run>/dq_report.md`; a `warn` row is a question for the data note, a `FAIL` never ships |
| After new rulings | `make extract`, later `make collect` |
| When prices change | update `src/licita/llm/prices.toml`, set `verified = true` once checked |
| Quarterly | rerun `make evals` on the golden set with current models; aliases move |
