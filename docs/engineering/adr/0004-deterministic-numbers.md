# ADR 0004: models read text; they never compute a number the reader sees

**Status:** accepted (carried over from the first design)

## Context
A procurement team will act on € figures and rates. A language model that writes "about 18% cheaper"
from memory cannot be audited.

## Decision
Every figure comes from `views.sql` and the deterministic fact sheets, with provenance (spine run id,
data date, scope). Models extract structure from rulings (grounds, holdings, quotes) and, later, write
narrative that may only cite numbers present in the fact sheet.

## Consequences
- Numbers are testable (hand-computed mini-spine) and guardrails can suppress misleading ones.
- The model's job is narrower and measurable (see EVALS), which is also what makes a small model viable.
