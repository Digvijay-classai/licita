# Licita

Procurement copilot for Spanish public hospitals (independent research project). Deferred work: `TODOS.md`.
Planning notes that name buyers live in the gitignored `private/` folder.

## Anonymity (hard rule)
No buyer, hospital, person, region, regional health service or tribunal is named in tracked files.
Region- and buyer-specific values belong in a deployment profile (`profiles/<name>.toml`, gitignored;
`profiles/example.toml` is the committed template). `python -m licita.anon check` must pass.

## Models
Mistral is the only hosted provider (`licita.llm`); `LICITA_LLM_PROVIDER=local` targets self-hosted open
weights. Model outputs are stored with dated model id, provider and prompt hash. `make check` = CI.

## Design System
Always read docs/engineering/DESIGN.md before making any visual or UI decisions.
All font choices, colors, spacing, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag any code that doesn't match docs/engineering/DESIGN.md.
