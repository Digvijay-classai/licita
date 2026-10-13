# Contributing

## Setup

```bash
make setup        # uv sync --all-extras, git hooks
make check        # lint, types, anonymity gate, tests, offline eval replay (what CI runs)
make demo         # see the app on synthetic data
```

Python 3.12 and [uv](https://docs.astral.sh/uv/) are the only requirements. Tests are offline: sockets
are blocked and no credential is read. HTTP is tested through `httpx.MockTransport`.

## Rules that CI enforces

- **Anonymity.** No buyer, person, region, regional health service or tribunal in tracked files. Put
  such values in a profile. `make anon` must pass. To protect a new name, add it to your private
  `private/denylist.txt` and regenerate `anon/denylist.sha256` with `licita anon hash private/denylist.txt`
  (the hashes are keyed: `licita anon keygen` once, and the same key as the `LICITA_ANON_KEY` CI secret).
  The only files the phrase scan skips are the national fact tables in `anon/allowlist.txt`; adding one
  needs a written reason on its line.
- **Numbers are deterministic.** Metrics live in `src/licita/spine/views.sql`; models never compute a
  figure shown to users.
- **Model outputs are evidence.** Store them with the dated model id, provider and prompt hash; validate
  against the schema before storage; verify quotes.
- **Errors are actionable.** Raise `LicitaError(problem, cause, fix)` for anything an operator can fix.
- **No secrets.** gitleaks runs in CI; keys come from the environment only.

## Commits and releases

Conventional commits (`feat:`, `fix:`, `docs:`...), one logical change each. Releases are semver tags
(`vX.Y.Z`); the release workflow publishes a signed image with an SBOM. Record user-visible changes in
`CHANGELOG.md` and decisions with lasting consequences as an ADR in `docs/engineering/adr/`.

## Changing the prompt or schema

Any change to `SYSTEM` or `SCHEMA` in `rulings/extract.py` changes the prompt hash. Rerun `make evals`
on the golden set and regenerate the replay fixtures if their hash note matters for your change.
