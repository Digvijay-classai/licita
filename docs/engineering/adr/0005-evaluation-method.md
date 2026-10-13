# ADR 0005: evaluation method

**Status:** accepted (2026-10)

## Decision
- A private, hand-labelled golden set of 30–50 tender-document rulings, sampled stratified by the
  tribunal's outcome, with a written labelling guide and ~10 double-labelled rulings.
- Metrics per model with Wilson 95% intervals; precision/recall withheld below 10 valid outputs.
- The tribunal's own outcome label as an independent check on our labels.
- Rows keyed by **dated** model ids (aliases resolved at run time) and a prompt hash; cost from a dated
  price table that states whether it was verified.
- CI replays cached synthetic responses through the same scoring; synthetic reports say so.

## Consequences
- Results are reproducible and comparable across model updates; small-n uncertainty is visible rather
  than hidden behind a point estimate.
- The golden set cannot be published (real rulings name buyers); the method and code can.
