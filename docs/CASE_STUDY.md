# Case study: putting competition evidence in front of a hospital before it drafts a tender

*How Licita would be taken from first conversation to production for a Spanish public hospital, and
what it is worth. Buyer, region and tribunal are anonymised; national figures are from the public data.*

## 1. The problem, in the customer's terms

Spanish public hospitals buy almost everything through public tenders. Across Spanish health buyers, an
open-procedure lot that receives **one bid is awarded at a median discount of 0.0 %**; with two bids the
median is 6.9 %, with four 13.8 % (140,952 lots from the 2017–2026 PLACSP archives). Most of that competition is decided before
publication, by drafting choices: brand-tied specifications, lot structure, solvency thresholds, deadlines,
and whether anyone thought to invite other suppliers.

Today the evidence arrives after the fact, as audit findings. The procurement team needs it at the moment
of drafting, in a form a hospital manager trusts.

## 2. Discovery

Run the way a deployment starts, before code:

- an interview guide for the hospital manager (30 min) and the head of procurement (45 min), built around
  the last real case ("tell me about the last tender that went wrong") rather than opinions;
- three hypotheses written down to validate or kill: expired contracts that keep running, single-bid
  tenders that cost money, and slow, risky drafting;
- a premise challenge and 24 recorded scope decisions before building (kept private: they discuss buyers).

## 3. Proof of value: what was built

| Need | What Licita does | Evidence it works |
|---|---|---|
| "Which contracts must we re-tender, and when?" | radar of contracts with no successor; start-by date = end − p80 lead time − prep | successor matcher tests, incl. a known false positive |
| "How do comparable hospitals tender this?" | peer benchmark by CPV and value band, topic peers for generic codes | hand-computed mini-spine tests |
| "What is one more bidder worth?" | € per year from the bid-count curve, with a bootstrap CI; suppressed when not significant | guardrail tests |
| "Who should we invite?" | suppliers that win comparable contracts elsewhere but never here; individuals excluded | tests |
| "Which clauses get annulled?" | Mistral extraction of grounds, holdings and verbatim quotes from tribunal rulings | golden-set evaluation (EVALS) |

## 4. Model choice: measured, not assumed

The extraction runs on Mistral with a strict JSON schema; every quote is checked against the source.
The decision rule: pick the smallest model whose decision-per-ground accuracy interval overlaps the best
model's, then compare cost per 1,000 rulings. The matrix (Small, Medium, Large, self-hosted Small) comes
from `make evals` on a private golden set, with Wilson intervals. An open 14B baseline was measured first
and rejected (0/12 decisions per ground correct), which is why the evaluation exists before scale.

If a small model is close but not enough, the next step is distillation: fine-tune a small Mistral model
on validated outputs of a larger one. That is documented as an option, not built.

## 5. Value case (illustrative)

Assumptions, stated so a finance director can change them:

| Input | Value |
|---|---|
| Annual value the hospital awards through open procedures | €10 M |
| Share of that value awarded with a single bid | 50 % |
| Share of single-bid value moved to two bidders by earlier, better-drafted tenders | 10 % |
| Median discount gain from one to two bids (national, health) | 6.9 − 0.0 = 6.9 points |

**Annual saving ≈ 10 M × 50 % × 10 % × 6.9 % ≈ €35,000**, from moving one in ten single-bid contracts. The first
two inputs are round placeholders, not any buyer's figures; the national curve is the only measured input.
The memo computes the same figure per contract from that hospital's own data, with a confidence interval,
and refuses to show it when the interval includes zero. The figure counts price only: it leaves out what
the radar exists for, contracts that expire with no successor, deserted lots and late re-tenders, whose
cost shows up as continuity spend, appeals and staff time rather than as a discount.

Running cost is small next to that: extracting the tribunal's rulings is one batch job priced in the eval report;
the app runs on a laptop or one VM. The real cost is the procurement team's time in a monthly review,
which is why the memo fits on one page.

## 6. Deployment

- **Hosted (EU API):** fastest pilot; only public text goes to the model.
- **Customer VPC or on-premises:** the same code with `LICITA_LLM_PROVIDER=local` and Mistral open weights
  on vLLM; nothing leaves the network. See [DEPLOYMENT](deploy/DEPLOYMENT.md) and
  [SECURITY](deploy/SECURITY.md).

## 7. Rollout: 30 / 60 / 90 days

| Window | Goal | Done when |
|---|---|---|
| Days 1–30 | Pilot on one hospital's next three re-tenders | memos reviewed in the procurement meeting; radar checked against the team's own list; golden-set results shared |
| Days 31–60 | Integrate into the drafting routine | monthly `make refresh` owned by the hospital; clause flags reviewed by the legal adviser on two live drafts; KPIs agreed (single-bid rate on re-tendered lots, days between start-by date and publication) |
| Days 61–90 | Scale to the regional health service | second and third hospital profiles; on-premises or VPC model decided with IT; quarterly re-evaluation of models scheduled |

KPIs reported to hospital management: single-bid rate on contracts that went through a memo vs comparable ones
that did not; share of re-tenders started by the start-by date; € estimate realised at award.

## 8. What would make it production-grade next

Clause linter with temporal-split evaluation, prompt-injection suite, cited chat over the spine with
parameterised tools only, and an open dataset release of the national health procurement data.
