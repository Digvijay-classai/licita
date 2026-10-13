# Governance

Short, concrete answers to what a public buyer's legal, data-protection and security teams ask.

## EU AI Act

- **What the system does:** decision support for procurement staff: statistics from public records,
  and structured extraction of public tribunal rulings by a general-purpose model.
- **Classification (our reading, not legal advice):** not a prohibited practice and not listed in
  Annex III high-risk areas; it neither decides on access to public services nor evaluates natural
  persons. Transparency duties apply to AI-generated text shown to users: the app labels model-derived
  content and keeps every number deterministic and sourced.
- **Human oversight:** a person reads every memo; nothing is published or decided automatically.
- **Provider obligations** for the general-purpose model sit with the model provider (Mistral AI).

## GDPR

- Inputs are public records. Rulings arrive pseudonymised by the tribunal; extraction never adds names.
- Natural persons among award winners (DNI/NIE) are excluded from supplier lists.
- No user accounts, no tracking (Streamlit usage stats disabled).
- Before any non-public document is processed (e.g. an unpublished draft), a DPIA and the DPA with the
  model provider are required, or the self-hosted path is used.

## Spanish public-sector security (ENS)

Licita does not hold classified or personal information. For a hosted deployment inside a public body,
run it within the body's ENS-certified infrastructure (option B or C in DEPLOYMENT) and treat the model
endpoint as an external service subject to the body's own approval.

## Accountability in the data

Every model output row records model id (dated), provider, prompt hash, text source, tokens, latency and
retries. Every fact sheet records the spine run id and data date. Evaluations are re-runnable from the
same rows.
