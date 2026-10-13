"""Normalise tribunal ruling fields into analysis labels.

Outcome labels are matched on the START of the text. Substring matching is wrong here because
"desestimación" and "desestimatoria" (dismissed) both contain "estima" (upheld).
"""
from __future__ import annotations

import re

from licita.profile import load_profile

HEALTH_RE = re.compile(r"hospital|salud|salut|sa[uú]de|osasun|sanitari|cl[ií]nic", re.I)
PLIEGOS_RE = re.compile(r"pliego|anuncio", re.I)

_OUTCOMES = [
    ("dismissed", re.compile(r"^desestim", re.I)),
    ("inadmissible", re.compile(r"^(inadmi|archivo|p[eé]rdida|desaparici|desistim|terminaci|aceptaci)", re.I)),
    ("partly_upheld", re.compile(r"^(estimaci[oó]n parcial|estimatoria parcial)", re.I)),
    ("upheld", re.compile(r"^(estima|nulidad)", re.I)),
]


def outcome(tipo_resolucion: str | None) -> str:
    t = (tipo_resolucion or "").strip()
    for label, rx in _OUTCOMES:
        if rx.search(t):
            return label
    return "unknown"


def is_health(organo: str | None) -> bool:
    terms = load_profile().region.health_terms
    text = organo or ""
    return bool(HEALTH_RE.search(text)) or any(term.lower() in text.lower() for term in terms)


def challenges_pliegos(acto: str | None) -> bool:
    return bool(PLIEGOS_RE.search(acto or ""))
