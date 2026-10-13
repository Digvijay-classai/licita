"""Scoring for ruling extraction against a hand-labelled golden set. Pure functions, no I/O.

Every proportion is reported with a Wilson 95% interval: with 30-50 rulings the interval is the result,
the point estimate alone would overstate what the evaluation shows.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

Z95 = 1.959964
MIN_N_FOR_PR = 10      # below this, precision/recall are printed as "n too small", not as numbers


@dataclass(frozen=True)
class Rate:
    k: int
    n: int

    @property
    def p(self) -> float | None:
        return self.k / self.n if self.n else None

    @property
    def ci(self) -> tuple[float, float] | None:
        return wilson(self.k, self.n)

    def fmt(self) -> str:
        ci, p = self.ci, self.p
        if ci is None or p is None:
            return "–"
        lo, hi = ci
        return f"{p:.0%} [{lo:.0%}–{hi:.0%}] ({self.k}/{self.n})"


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def ground_counts(gold: list[dict], pred: list[dict]) -> tuple[int, int, int]:
    """(true positives, predicted, gold) on ground categories, as multisets (a ruling can repeat a category)."""
    g, p = Counter(x["ground"] for x in gold), Counter(x["ground"] for x in pred)
    return sum((g & p).values()), sum(p.values()), sum(g.values())


def holding_matches(gold: list[dict], pred: list[dict]) -> tuple[int, int]:
    """Per-ground decision accuracy on categories both sides found: (correct, compared).

    Within a category, gold and predicted holdings are compared as sorted lists, pairwise.
    """
    correct = compared = 0
    for cat in {x["ground"] for x in gold} & {x["ground"] for x in pred}:
        gh = sorted(x["holding"] for x in gold if x["ground"] == cat)
        ph = sorted(x["holding"] for x in pred if x["ground"] == cat)
        for a, b in zip(gh, ph, strict=False):
            compared += 1
            correct += int(a == b)
    return correct, compared


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """Agreement between two labellers beyond chance (outcome labels)."""
    if not a or len(a) != len(b):
        return None
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(a) | set(b)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)
