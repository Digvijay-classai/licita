# Security and privacy

## Credentials

- **One credential: `MISTRAL_API_KEY`.** Read from the environment only (a secret manager or the shell),
  never from a file in the repository. A blank key is an error, not a silent fallback. `licita config`
  reports whether a key is set and never prints it. Use one key per environment and workspace, with a
  spend limit in the Mistral console; rotate by issuing a new key and revoking the old one.
- **No key needed** for the demo, the test suite, CI, or the self-hosted path (`LICITA_LLM_PROVIDER=local`).
  An optional bearer token for the local server goes in `LICITA_LLM_API_KEY`.
- This replaces the earlier keyless design (OAuth / workload identity with another vendor); the
  trade-off is recorded in [ADR 0002](../engineering/adr/0002-static-mistral-key.md).
- **Secret scanning:** gitleaks in pre-commit (when installed) and always in CI on the full history.
- **TLS:** verification is never disabled. When a public host omits an intermediate certificate, the
  deployer places the public intermediate (fetched from the leaf's AIA URL, checked against the Mozilla
  root store) under `data/state/certs/` or `LICITA_CA_EXTRA`; no host-specific certificate ships with the code.
- **Anonymity key:** `LICITA_ANON_KEY` (CI secret) or `private/anon.key` keys the denylist hashes; it is not
  a credential for any service, but rotating it means re-hashing the denylist.

## Untrusted input

Tender documents, tribunal rulings and every field of the procurement feeds are written by third parties.

- Ruling text reaches the model inside a `<ruling_text>` envelope the system prompt declares to be data.
  Extraction calls carry **no tools**; output is constrained by a strict JSON schema and then validated
  in full before storage (a test pins the absence of tools in the request body).
- Quotes are verified against the exact text sent (embedded PDF text or OCR); unverifiable quotes are
  flagged and never shown as evidence.
- Downloads are size-capped, stored with a SHA-256 manifest, never executed; HTML error pages are rejected.
- Every data-derived string rendered in the app is HTML-escaped; a test injects markup through a title.
- OCR input is sent as bytes inline, so the provider never fetches a URL on our behalf.

## Data flows and residency

| Flow | Data | Leaves the machine? | Where to |
|---|---|---|---|
| PLACSP ingest | public procurement records | download only | — |
| Tribunal scrape | public rulings (pseudonymised by the tribunal) | download only | — |
| Extraction / OCR (hosted) | public ruling text or PDF bytes | yes | Mistral AI La Plateforme (EU provider); batch input/output files are deleted after collection |
| Extraction (self-hosted) | public ruling text | no | your vLLM/Ollama server |
| Fact sheets, memos, app | derived from public data; buyer-specific | no | local disk (`out/`, gitignored) |

Retention, training use and sub-processors on the hosted API are governed by the customer's agreement
with Mistral AI; confirm the current terms (DPA, zero-retention options) before sending anything beyond
public text. Future features that would send non-public drafts (e.g. unpublished tender documents) are
blocked on that review (see TODOS) and should default to the self-hosted path.

## Privacy

- Public data only; no accounts, no shared state (one local build per buyer).
- Rulings are pseudonymised by the tribunal; extraction never adds names.
- Natural persons among past award winners are excluded from supplier invite lists.

## Anonymity of the repository

No buyer, person, region, regional health service or tribunal is named in tracked files. Region- and
buyer-specific values live in gitignored profiles. `licita anon check` enforces it in pre-commit and CI
against a hashed denylist and refuses data binaries. Remaining inference risks: [RESIDUAL_RISK](RESIDUAL_RISK.md).

## Publication rules

- Code, tests and documentation are public. Data, spines, run reports, fact sheets, tender documents,
  profiles, the golden set and any buyer-specific output stay out of git and out of public artifacts.
- Independent, non-commercial research project; no hospital commissions or endorses it.
- Decision support only: it does not replace the legal report a tender requires.
