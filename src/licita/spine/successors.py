"""Match each expiring contract to its successor (design review 4A, outside voice T7#11).

Statuses:
  extended               a later "Prórroga …" entry for the same object (end date moves)
  replaced               a later tender for the same object by the same buyer
  replaced_partial       a later tender covering part of it (lot overlap)
  absorbed_by_framework  bought later via a framework agreement (procedure 7) or a central regional buyer
  in_progress            a successor is published but not yet awarded
  no_successor           nothing plausible found (claimed only when confidence is high)
  verify                 a weak candidate exists; a human must check ("verificar")

Matching is folder-level. Titles lose administrative boilerplate before character 3-gram Jaccard,
because Spanish tender titles share long stock phrases ("Suministro de … con destino al Hospital …").
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache

from licita.profile import load_profile

BOILERPLATE = {
    "suministro", "servicio", "servicios", "contratacion", "contrato", "adquisicion", "expediente",
    "de", "del", "la", "las", "el", "los", "y", "e", "o", "a", "en", "para", "con", "por", "al", "su",
    "destino", "destinados", "destinado", "hospital", "universitario", "universitaria", "clinico",
    # a region's own buyer-name words (management-unit nouns, city names) come from profile.boilerplate_terms
    "centros", "centro", "dependientes", "area", "salud", "lote", "lotes", "procedimiento",
    "abierto", "simplificado", "mediante", "acuerdo", "marco", "basado", "derivado", "n", "no",
    # generic object words: the category code already says "maintenance" vs "supply"
    "mantenimiento", "integral", "preventivo", "correctivo", "equipos", "equipo", "material",
    "materiales", "sistema", "sistemas", "diverso", "diversos", "varios", "instalacion", "instalaciones",
    # extension/modification wording (detected separately on the raw title)
    "prorroga", "prorrogas", "referente", "relativo", "relativa", "modificacion", "contratos",
}
PRORROGA_RE = re.compile(r"^\s*(pr[oó]rroga|prorrogar)", re.I)


@cache
def _boilerplate(extra: tuple[str, ...]) -> frozenset[str]:
    return frozenset(BOILERPLATE | {_fold(w) for w in extra})


@cache
def _central_re(pattern: str) -> re.Pattern | None:
    return re.compile(pattern, re.I) if pattern else None

STRONG, WEAK = 0.5, 0.3


def _fold(text: str) -> str:
    s = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def normalise(title: str | None) -> str:
    """Title words without stock phrases; the profile adds its place and hospital names to the stoplist."""
    stop = _boilerplate(load_profile().region.boilerplate_terms)
    words = [w for w in re.findall(r"[a-z0-9]+", _fold(title or "")) if w not in stop and not w.isdigit()]
    return " ".join(words)


def grams(s: str, n: int = 3) -> set[str]:
    s = f"  {s}  "
    return {s[i:i + n] for i in range(len(s) - n + 1)} if s.strip() else set()


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass
class Contract:
    buyer_nif: str
    folder_id: str
    title: str
    cpv4s: frozenset[str]
    award_d: date | None
    end_initial: date | None
    end_max: date | None
    n_lots: int = 0


@dataclass
class Candidate:
    buyer_nif: str
    buyer: str
    folder_id: str
    title: str
    cpv4s: frozenset[str]
    first_seen: date | None   # earliest notice / snapshot date
    status: str | None        # PLACSP folder status (PRE, PUB, EV, ADJ, RES, ANUL)
    procedure: str | None
    n_lots: int = 0


@dataclass
class Match:
    status: str
    confidence: float
    successor_folder: str | None = None
    successor_title: str | None = None
    score: float = 0.0


def score(c: Contract, k: Candidate, cg: set[str]) -> float:
    """Title similarity, discounted when the category codes disagree. A shared code alone never
    makes a match (it multiplies, it doesn't add)."""
    title = jaccard(cg, grams(normalise(k.title)))
    if c.cpv4s & k.cpv4s:
        factor = 1.0
    elif {x[:2] for x in c.cpv4s} & {x[:2] for x in k.cpv4s}:
        factor = 0.85
    else:
        factor = 0.6   # maintenance can be coded 33 (device) or 50 (service): don't zero it
    return title * factor


def classify(c: Contract, k: Candidate, s: float) -> str:
    if PRORROGA_RE.match(k.title or ""):
        return "extended"
    central = _central_re(load_profile().region.central_buyer_re)
    if k.procedure == "7" or (central and central.search(k.buyer or "")):
        return "absorbed_by_framework"
    if k.status in ("PRE", "PUB", "EV"):
        return "in_progress"
    if c.n_lots and k.n_lots and k.n_lots != c.n_lots:
        return "replaced_partial"
    return "replaced"


def match(c: Contract, candidates: list[Candidate], open_ended: bool = False) -> Match:
    """Best successor among candidates published after the predecessor was awarded.

    The main window runs to a year after the latest possible end. With `open_ended`, a contract with no
    match in that window gets a second pass over everything published later: a strong match there means
    the need was re-tendered after a gap (bridged by negotiated or minor contracts), status replaced_late.
    """
    if c.award_d is None:
        return Match("verify", 0.0)
    lo = c.award_d + timedelta(days=1)
    hi = (c.end_max or c.end_initial or c.award_d) + timedelta(days=365)
    cg = grams(normalise(c.title))

    def best_in(window) -> tuple[float, Candidate] | None:
        best = None
        for k in candidates:
            if k.folder_id == c.folder_id or k.first_seen is None or not window(k.first_seen):
                continue
            s = score(c, k, cg)
            if best is None or s > best[0]:
                best = (s, k)
        return best

    best = best_in(lambda d: lo <= d <= hi)
    if best is None or best[0] < WEAK:
        if open_ended:
            late = best_in(lambda d: d > hi)
            if late and late[0] >= STRONG:
                s, k = late
                return Match("replaced_late", s, k.folder_id, k.title, s)
        # Confident only if there was something to compare against and nothing came close.
        conf = 0.9 if best is None or best[0] < 0.2 else 0.6
        return Match("no_successor", conf, score=best[0] if best else 0.0)
    s, k = best
    if s < STRONG:
        return Match("verify", s, k.folder_id, k.title, s)
    return Match(classify(c, k, s), s, k.folder_id, k.title, s)
