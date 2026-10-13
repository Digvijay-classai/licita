# ADR 0003: deployment profiles and a hashed denylist keep the repository anonymous

**Status:** accepted (2026-10)

## Context
The first build was written for specific hospitals in one region. Their names, tax ids, the region's
statistical codes, the tribunal URL and place names lived in code, tests and docs, including inside
logic (regex filters, stopword lists, a NUTS prefix in SQL).

## Decision
1. Everything buyer- or region-specific moves to a TOML **profile** (`profiles/<name>.toml`, gitignored;
   `example.toml` committed with fictitious values). Code reads it lazily.
2. Tests use an invented region and buyers.
3. A **gate** (`licita anon check`) tokenises every tracked file and compares 1–3 word phrases, accent-
   and case-folded, against keyed hashes (HMAC-SHA256) of a private denylist. The key never enters the
   repository (`private/anon.key` locally, a CI secret otherwise), so the committed hash file is useless to a
   reader while CI can still enforce the rule. Data binaries are refused outside `demo/`.

4. National fact tables that must name every region to identify none (the city → NUTS-2 table) are the
   only files the phrase scan skips, listed with a reason in `anon/allowlist.txt`; a table that omitted
   exactly one region would point at the deployment.

## Consequences
- A new region is a new profile, not a code change (a parse-time filter change needs a re-parse).
- The first version used unkeyed SHA-256; an audit before publication recovered a large share of the
  phrases from it with a short list of Spanish place names in minutes, which is why the hashes are keyed now. A clean gate run
  is also an oracle: it proves the tree holds none of the protected phrases, so any phrase that does appear
  in the tree is known to be unprotected. Pull requests from forks cannot run the gate (no secret); a
  maintainer runs it before merging.
- Git history predates this; the public repository is a fresh-history import of a tree that passes the
  gate, audited independently before publication. The development history stays private.
