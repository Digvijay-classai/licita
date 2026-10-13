# ADR 0001: Mistral as the only hosted model provider

**Status:** accepted (2026-10) · **Supersedes:** the earlier single-vendor client for another provider

## Context
The extraction workload is Spanish legal text, high volume, batchable, and destined for public-sector
buyers who ask where data goes and whether the model can run on their premises.

## Decision
`licita.llm` has two providers: `mistral` (La Plateforme: chat with strict JSON schema, Batch API, OCR)
and `local` (any OpenAI-compatible server, used with Mistral open weights). No other vendor SDK is a
dependency; the HTTP calls are plain REST so request shapes are visible and testable offline.

## Consequences
- One code path for hosted and sovereign deployments: switching is an environment variable.
- Batch API halves extraction cost; OCR covers scanned rulings without a second vendor.
- Cross-vendor comparison is no longer built in. The open-model baseline (Qwen, measured) and the
  self-hosted Mistral run provide the comparison that matters to buyers: hosted vs on-premises.
- REST over an SDK means we maintain request/response parsing; tests pin the shapes.
