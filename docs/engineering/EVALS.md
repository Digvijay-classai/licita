# Evaluations

What is measured, how, and what the numbers so far say. Every model output is stored with the dated model
id, provider, prompt hash and text source, so models are compared on identical rows.

## What is evaluated

| Component | Question | Method | Status |
|---|---|---|---|
| Spine metrics | Are the numbers computed as defined? | Hand-computed mini-spine in tests; coverage gates on every build | in CI |
| Successor matching | Does the radar know a contract was already re-tendered? | Fixture pairs incl. a known false positive | in CI |
| Fact-sheet guardrails | Are misleading numbers suppressed? | Unit tests per guardrail | in CI |
| Ruling extraction | Grounds, decision per ground, verbatim quotes, outcome | Golden set (below), schema validation, quote grounding, tribunal outcome label | harness in CI (replay); live run needs `MISTRAL_API_KEY` |
| Batch robustness | Does collection survive bad output? | Hand-written fixtures in Mistral's batch file format: invalid JSON, truncation, error file, unknown/duplicate ids, unanswered requests | in CI |
| Clause linter | Would it have flagged clauses the tribunal later annulled? | Temporal split: pliegos before annulment vs unchallenged tenders in the same CPV-4 | next |
| Prompt injection | Does text inside a document change behaviour? | 10 injection cases in ruling/tender text | next |

## Ruling extraction: the model matrix

Run it with a key:

```bash
make label                                   # 40 rulings sampled, stratified by tribunal outcome
#   label them (guide below), move labelled lines to private/golden/rulings.jsonl
make evals MODELS=mistral-small-latest,mistral-medium-latest,mistral-large-latest
make evals-tribunal MODELS=...              # no hand labels: outcome vs the tribunal's published label
LICITA_LLM_PROVIDER=local make evals MODELS=mistralai/Mistral-Small-3.2-24B-Instruct-2506   # self-hosted
```

The report (also written to `runs/evals/`) has one row per model:

| Column | Definition |
|---|---|
| valid output | completions that parsed and passed the full JSON schema, of those attempted |
| outcome | predicted outcome equals the golden label |
| ground precision / recall | ground categories as multisets, pooled over rulings |
| decision per ground | holding correct on categories both sides found (the linter learns from this) |
| quotes verified | returned quotes that fuzzy-match (≥ 90) the exact text the model saw |
| cost / 1,000 rulings | from token usage and the dated price table; batch rows at the batch discount |
| latency p50 / p95 | synchronous calls only; retries are counted separately |

Every proportion carries a Wilson 95% interval. Precision, recall and decision accuracy are withheld
below 10 valid outputs. The report adds agreement with the tribunal's own published outcome (an
independent check on our labels) and, for double-labelled rulings, Cohen's kappa between labellers.

### Results: tribunal reference, 2026-10-14

`make evals-tribunal MODELS=ministral-3b-2512,ministral-8b-2512,ministral-14b-2512` on the 39 sampled
rulings with a mappable published outcome (one of 40 had none). Reference: the tribunal's published outcome
(ADR 0005 amendment); ground columns are withheld until an expert labels the golden set.

| Model (dated id) | Valid output | Outcome = tribunal's label | Quotes verified | Cost / 1,000 rulings | Latency p50 / p95 |
|---|---|---|---|---|---|
| `ministral-3b-2512` | 100 % [91–100] (39/39) | 87 % [73–94] (34/39) | 71 % [55–84] (25/35) | $0.64 | 2.2 s / 3.9 s |
| `ministral-8b-2512` | 100 % [91–100] (39/39) | 92 % [80–97] (36/39) | 68 % [54–79] (34/50) | $0.97 | 5.5 s / 11.0 s |
| `ministral-14b-2512` | 100 % [91–100] (39/39) | 92 % [80–97] (36/39) | 88 % [75–95] (37/42) | $1.28 | 5.7 s / 17.0 s |

- Small 4 and Medium 3.5 returned HTTP 429 with a per-model limit of 0 requests/minute on the evaluation
  workspace; Large returned 403 (tier). Same command once they are enabled.
- Outcome misses: 8B and 14B, 3 rulings each, the same 3 (published "Estimación", predicted partly upheld).
  3B adds both loss-of-object terminations (predicted dismissed; reference inadmissible) and one upheld
  ruling read as dismissed. Whether the published label or the models are right on the shared 3 is a
  question for the expert labeller.
- Stability: an earlier identical run (stored under the `-latest` aliases before alias pinning was fixed)
  gave the same outcome on 39/39 rulings for 8B and 14B, 38/39 for 3B; verified quotes 72 / 75 / 84 %.
- Cost from the dated price table (checked and confirmed 2026-10-14); all calls synchronous. Prompt hash
  `b6474d49462871fa`.

CI runs the same scoring on synthetic fixtures (`tests/fixtures/evals/`, marked as synthetic in every
report); those numbers are not model results.

## Labelling guide (golden set)

The golden set stays private (`private/golden/rulings.jsonl`): real rulings name contracting bodies.

For a domain expert who does not use the command line: `make label-kit` (or `L=B N=10` for the second
labeller) writes one plain-text sheet per ruling, operative part first, a Spanish guide
([LABELLING_es.md](LABELLING_es.md)) and a `;`-separated answer sheet that opens in Spanish Excel. The
sheets contain no model output and no tribunal metadata label. `make label-import ANSWERS=... L=A` checks
the filled sheet, lists every bad row, and merges it (labeller B attaches as `second`).

1. Read the whole ruling. Label only grounds the tribunal **decides** (a "motivo" with its own
   reasoning and conclusion). Arguments it declines to examine are `not_decided`.
2. `ground`: one category per decided ground (see `GROUNDS` in `src/licita/rulings/extract.py`). If one
   motivo covers two categories, choose the one the tribunal's reasoning turns on.
3. `holding`: `upheld` when the tribunal agrees with the appellant on that ground, `dismissed` when it
   does not, `not_decided` otherwise.
4. `outcome`: the operative part (fallo), not the summary page: `upheld`, `partly_upheld`, `dismissed`,
   `inadmissible`, `other`.
5. Write down every hard call in a `notes` field. Re-label 10 rulings a week later (or ask a second
   person) into `second`; the report computes agreement.
6. Do not look at any model output before labelling a ruling.

Limits stated up front: the labeller is not a lawyer; 30–50 rulings give intervals of roughly ±15
points; categories are ours, not the tribunal's.

## Open-model baseline: Qwen3-14B (measured)

Before any API spend, the extraction was run with an open model on free Kaggle GPUs (2 × T4, Ollama,
Q4 weights) with the identical system prompt, JSON schema and input envelope. Five rulings, scored against
the ruling text and the tribunal's published outcome:

| Measure | Qwen3-14B | Qwen3-14B + thinking |
|---|---|---|
| Valid output | 5/5 | 4/5 (one run looped for 23 min and hit the token limit) |
| Quotes verified against source | 5/6 | 5/5 |
| Decision per ground correct | **0/12** grounds as returned, all "not_decided" (9 after collapsing repeats) | **0/7** (all "not_decided") |
| Overall outcome matches tribunal | 2/5 | 2/4 |
| Median time per ruling (Kaggle runner log) | 95 s | 70 s |

The per-ground decision is what the clause linter learns from, so this baseline is not fit for purpose.
Two checks from this run are permanent: repeated grounds are collapsed and counted, and every output is
schema-validated before storage. The next open-weight comparison is Mistral Small 3.2 on vLLM, which
runs the same code path as hosted Mistral (`LICITA_LLM_PROVIDER=local`).

## Principles

- A model output is stored with the dated model id that produced it; aliases are resolved first.
- A quote that does not appear in the text the model saw is flagged, never shown as evidence.
- Live evaluations cost money and are run explicitly (`make evals`), never in CI.
- Prices are dated; reports say whether they were confirmed against the provider's pricing page.
