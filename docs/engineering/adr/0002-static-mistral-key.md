# ADR 0002: a static API key for hosted Mistral

**Status:** accepted (2026-10)

## Context
The previous design refused static keys and used short-lived OAuth / workload identity with another
provider. Mistral's API authenticates with API keys. A reviewer will notice the change.

## Decision
`MISTRAL_API_KEY` is the single credential, read from the environment (secret manager in production),
never from repository files. Blank values are errors. `licita config` never prints it. The self-hosted
path needs no key at all.

## Consequences
- Operational controls replace short-lived tokens: one key per environment and workspace, console spend
  limits, rotation by reissue and revoke, secret scanning in pre-commit and CI.
- Customers who require no long-lived secrets use option B/C in DEPLOYMENT (endpoint in their tenancy,
  or vLLM on premises).
