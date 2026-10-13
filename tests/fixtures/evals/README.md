Synthetic evaluation fixtures (invented rulings, labels and model responses).
They exercise the scoring pipeline offline in CI. They are NOT model results and must never be quoted as such.
Real results come from `python -m licita.evals.run live` on the private golden set.
