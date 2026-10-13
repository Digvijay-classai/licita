# Residual re-identification risk

The repository names no buyer, region or tribunal, and a keyed-hash denylist gate keeps it that way. That is
not the same as "impossible to infer". Two independent audits of the public tree (October 2026) looked for
direct identifiers and for inference chains; what they found was fixed, and what an informed reader could
still deduce is listed here with the reason it is accepted.

| Signal | What it reveals | Mitigation | Accepted because |
|---|---|---|---|
| The tribunal scraper | the shape of a rulings site: numbered rulings per year, labelled fields, a PDF | URL templates, number and PDF regexes, field labels and stop words all come from the profile; fixtures use an invented layout | the code describes a class of sites, not one site |
| TLS handling | that some public host the deployment reads misconfigures its certificate chain | no certificate ships with the code; the deployer adds intermediates under `data/state/certs/` | a common misconfiguration, not a fingerprint |
| Health-buyer regexes and the title stoplist | generic nouns in Spain's co-official languages | a region's own naming conventions and acronyms live in the profile (`health_terms`, `boilerplate_terms`) | national coverage logic, no region singled out |
| The city → NUTS-2 table | geography | one rule for all nineteen regions (capitals, regional seats, cities ≥ 100,000, community names), tested for coverage and even depth | a fact table that is the same for every region identifies none |
| PLACSP feed 1044 (regional platforms) | that some regions publish through it | none needed | it aggregates many regions |
| Statistics in docs | national figures; the value case uses round placeholders | national scope, no buyer-level counts | national data is public and not identifying |
| Test fixtures and the demo | invented buyers, tenders, rulings and dates | fixtures name "Santa Ficticia" and "Villaejemplo"; contract objects are generic | nothing is copied from a real buyer's history |
| The keyed denylist (`anon/denylist.sha256`) | nothing without the key; with it, which phrases are protected | HMAC-SHA256 with a key outside the repository; a clean gate run is an oracle for what is *not* protected | the key is a CI secret and a private file, never committed |
| Structural facts | that the deployment region has its own procurement tribunal whose rulings are online, that its hospitals publish directly on the national platform under their own tax ids, and that a regional purchasing body appears in the benchmark | none that keeps the code honest | these properties intersect to a short list of regions, not to one; a motivated reader with time could narrow further, which is why nothing buyer-level is in the tree |
| Real tokens that appear in tests (a capital city, a co-official-language health noun) | by the gate's oracle, that those are *not* the deployment's | examples use the largest region's capital and generic nouns only | excluding the largest region or a language community is not identifying |
| The author | the owner's name and GitHub handle | the owner's choice | the project is independent, non-commercial research and says so |
| Git history | nothing: the public repository is a single-commit import | the development history stays in a private repository that is never made public or forked | history never leaves the private repo |

Anyone deploying Licita should treat profiles, `out/`, `data/`, the golden set and `private/` as confidential.
