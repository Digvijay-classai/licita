"""Buyer identity resolution: one key per contracting authority, however PLACSP labelled it.

PLACSP identifies a buyer three ways and not consistently: a NIF (feed 643 since 2018), a DIR3 code (both
feeds, increasingly since 2023) and a free-text name with a parent chain (always). Feed 1044 never carries a
NIF. The same authority therefore shows up under a NIF key in one snapshot, a DIR3 key in another and a
name key in a third, and a tender can sit under two keys at once. On the 2017–2026 archives that split 567
keys and 4,405 folders away from their real buyer and double-counted 5,247 tenders.

Rules, applied in this order and documented in docs/engineering/DATA.md:
  1. A NIF is an identity. Two NIFs are never merged, whatever else they share.
  2. A DIR3-only key joins the NIF it co-occurs with, when that DIR3 was ever published next to exactly one NIF.
  3. A name-only key joins an identified key (NIF or resolved DIR3) when its normalised name, city and first
     parent all match exactly one identified key; or, for long names (>= 20 characters), when name and city
     match exactly one. Short or generic names ("director gerente") stay apart.
Anything unresolved keeps its raw key. The mapping is written to the spine as `buyer_xref`.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

LONG_NAME = 20


@dataclass(frozen=True)
class KeyRow:
    """One (raw key, name, city, parent) combination seen in the snapshots, with its NIF/DIR3 if any."""
    raw_key: str
    nif: str | None
    dir3: str | None
    name: str
    city: str
    parent1: str
    n: int = 1


def kind(raw_key: str) -> str:
    if raw_key.startswith("DIR3:"):
        return "dir3"
    if raw_key.startswith("NAME:"):
        return "name"
    return "nif"


def resolve(rows: Iterable[KeyRow], nif_dir3_pairs: Iterable[tuple[str, str]]) -> tuple[dict[str, str], dict[str, int]]:
    """Map every raw key to its canonical key. Returns (mapping, stats)."""
    rows = list(rows)
    canonical: dict[str, str] = {}
    stats = Counter()
    nifs_by_dir3: dict[str, set[str]] = defaultdict(set)
    for nif, dir3 in nif_dir3_pairs:
        nifs_by_dir3[dir3].add(nif)

    for r in rows:
        canonical.setdefault(r.raw_key, r.raw_key)

    # 2. DIR3-only keys -> the single NIF they co-occur with
    for r in rows:
        if kind(r.raw_key) == "dir3" and r.dir3:
            nifs = nifs_by_dir3.get(r.dir3, set())
            if len(nifs) == 1:
                target = next(iter(nifs))
                if canonical[r.raw_key] != target:
                    canonical[r.raw_key] = target
                    stats["dir3_merged"] += 1
            elif len(nifs) > 1:
                stats["dir3_ambiguous"] += 1

    # 3. name-only keys -> the single identified key with the same (name, city, parent) or long (name, city)
    exact: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    loose: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in rows:
        if kind(r.raw_key) != "name":
            ident = canonical[r.raw_key]
            exact[(r.name, r.city, r.parent1)].add(ident)
            loose[(r.name, r.city)].add(ident)
    decided: dict[str, str | None] = {}
    for r in rows:
        if kind(r.raw_key) != "name" or r.raw_key in decided:
            continue
        cands = exact.get((r.name, r.city, r.parent1), set())
        if len(cands) != 1 and len(r.name) >= LONG_NAME:
            cands = loose.get((r.name, r.city), set())
        if len(cands) == 1:
            decided[r.raw_key] = next(iter(cands))
        elif len(cands) > 1:
            decided[r.raw_key] = None
            stats["name_ambiguous"] += 1
    for raw, target in decided.items():
        if target and target != raw:
            canonical[raw] = target
            stats["name_merged"] += 1

    # a merged key's canonical may itself have been merged (dir3 -> nif happens first, so one hop suffices)
    for raw in list(canonical):
        tgt = canonical[raw]
        if canonical.get(tgt, tgt) != tgt:
            canonical[raw] = canonical[tgt]
    stats["keys"] = len(canonical)
    stats["canonical"] = len(set(canonical.values()))
    return canonical, dict(stats)
