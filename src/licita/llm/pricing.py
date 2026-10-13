"""Cost per call from a dated price table (USD per million tokens). Prices change: the table says when it was
checked and whether it was confirmed against Mistral's pricing page, and reports repeat that caveat.

Model ids are matched by the longest prefix, so `mistral-small-2603` uses the `mistral-small` row.
Self-hosted models cost GPU time, not tokens: they report no token price.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PRICES = Path(__file__).with_name("prices.toml")


@dataclass(frozen=True)
class Price:
    prefix: str
    input_per_m: float
    output_per_m: float


@dataclass(frozen=True)
class PriceTable:
    checked: str
    verified: bool
    source: str
    batch_discount: float
    rows: tuple[Price, ...]

    def find(self, model: str) -> Price | None:
        hits = [p for p in self.rows if model.startswith(p.prefix)]
        return max(hits, key=lambda p: len(p.prefix)) if hits else None

    def cost(self, model: str, input_tokens: int | None, output_tokens: int | None, batch: bool = False) -> float | None:
        p = self.find(model)
        if p is None or input_tokens is None or output_tokens is None:
            return None
        usd = (input_tokens * p.input_per_m + output_tokens * p.output_per_m) / 1e6
        return usd * (1 - self.batch_discount) if batch else usd

    @property
    def caveat(self) -> str:
        state = "confirmed" if self.verified else "NOT confirmed"
        return f"prices checked {self.checked}, {state} against {self.source}"


@cache
def load(path: Path = PRICES) -> PriceTable:
    raw = tomllib.loads(path.read_text())
    rows = tuple(Price(k, float(v["input"]), float(v["output"])) for k, v in raw["models"].items())
    return PriceTable(raw["checked"], bool(raw["verified"]), raw["source"], float(raw["batch_discount"]), rows)
