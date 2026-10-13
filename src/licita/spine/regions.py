"""Region assignment and coverage scoring for the multi-buyer benchmark.

A buyer's region is a NUTS-2 code (Spain: ES11 … ES70). PLACSP publishes the NUTS of the place of
performance on most tenders, but a share carry a country-level or malformed code, and some carry none.
Resolution order (first hit wins):
  1. the tender's own NUTS when it is a Spanish code of at least four characters;
  2. the deployment profile's `nuts_prefix` when the buyer's parent chain matches `region.buyer_chain_re`
     (the profile's region keeps its own names out of this file);
  3. a table of Spanish provincial capitals, regional seats and cities of 100,000 or more for all nineteen
     NUTS-2 regions (geographic facts, `city_nuts2.csv`), matched on the buyer's city without accents or case;
  4. None: the buyer is shown without a region and never enters a regional comparison.

Coverage score: the mean of five indicators a buyer's lots either have or lack, in [0, 1]. It decides whether
a buyer is comparable at all (see benchmark.py) and is shown next to every band so a low position can never be
produced by publishing less.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path

from licita.profile import Profile


# city or province (lower case, no accents) -> NUTS-2, from city_nuts2.csv. One rule for every region: the 50
# provincial capitals, the regional seats, municipalities of 100,000 inhabitants or more, and the community
# names with their usual spellings. A geographic fact table, so it names every region, including a deployment's
# own; the anonymity gate allowlists this one file for that reason (anon/allowlist.txt). Smaller towns resolve
# through the tender's own NUTS code or the profile's buyer-chain rule, never through this table.
def _load_city_table() -> dict[str, str]:
    import csv
    with (Path(__file__).with_name("city_nuts2.csv")).open(encoding="utf-8", newline="") as f:
        return {row["city"]: row["nuts2"] for row in csv.DictReader(f)}


CITY_NUTS2: dict[str, str] = _load_city_table()

_NUTS_RE = re.compile(r"^ES[0-9]{2}")


def normalise_city(city: str | None) -> str:
    if not city:
        return ""
    s = unicodedata.normalize("NFKD", city.casefold())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.strip())


def region_for(nuts: str | None, buyer_city: str | None, buyer_parents: str | None,
               profile: Profile | None) -> str | None:
    if nuts and _NUTS_RE.match(nuts.strip().upper()):
        return nuts.strip().upper()[:4]
    if (profile is not None and profile.region.nuts_prefix and profile.region.buyer_chain_re
            and re.search(profile.region.buyer_chain_re, buyer_parents or "", re.IGNORECASE)):
        return profile.region.nuts_prefix[:4]
    return CITY_NUTS2.get(normalise_city(buyer_city))


COVERAGE_FIELDS = ("has_budget", "has_real_award_date", "has_notice_date", "has_duration", "not_feed_1044")


def coverage_score(row: Mapping[str, float | None]) -> float:
    """Mean of the five coverage shares (each 0..1); missing values count as 0."""
    vals = [float(row.get(k) or 0.0) for k in COVERAGE_FIELDS]
    return round(max(0.0, min(1.0, sum(vals) / len(vals))), 3)
