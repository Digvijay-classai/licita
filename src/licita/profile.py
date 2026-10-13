"""Deployment profiles: everything that ties a build to one region and its buyers lives here, not in code.

A profile is a TOML file. `profiles/example.toml` ships with the repo and uses placeholders; real profiles sit
next to it and are gitignored, so no buyer, region or tribunal is named in the source tree.

  LICITA_PROFILE=example        a name under profiles/ (default)
  LICITA_PROFILE=/path/x.toml   or a path

Nothing reads the profile at import time: a missing or broken profile must not break `--help` or the tests.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from licita.config import ROOT
from licita.errors import LicitaError

PROFILES_DIR = ROOT / "profiles"
DEFAULT_PROFILE = "example"


class ProfileError(LicitaError):
    pass


@dataclass(frozen=True)
class Buyer:
    key: str
    name: str
    nif: str
    short: str


@dataclass(frozen=True)
class Region:
    label: str = ""
    nuts_prefix: str = ""
    buyer_chain_re: str = ""
    central_buyer_re: str = ""
    health_terms: tuple[str, ...] = ()
    boilerplate_terms: tuple[str, ...] = ()


FIELD_KEYS = ("fecha", "organo", "acto", "objeto", "tipo_contrato", "tipo_resolucion")
DEFAULT_LABELS = {"fecha": "Fecha:", "organo": "Órgano:", "acto": "Acto:", "objeto": "Objeto:",
                  "tipo_contrato": "Contrato:", "tipo_resolucion": "Resolución:"}


@dataclass(frozen=True)
class Tribunal:
    """One tribunal's website, described entirely by the profile so no site is recognisable from the code.

    `year_url` and `ruling_url` are format templates over {base}, {year} and {num}; `number_re` reads ruling
    numbers off a year's listing (may use {year}); `pdf_re` captures the PDF href on a ruling page (may use
    {year} and {num}); `labels` are the field labels on a ruling page; `stop_terms` end a field's text."""
    name: str = ""
    short: str = ""
    base_url: str = ""
    first_year: int | None = None      # first year to scrape; required by `licita.rulings.scrape`
    year_url: str = "{base}/{year}"
    ruling_url: str = "{base}/{year}/{num}"
    number_re: str = r"/{year}/(\d+)"
    pdf_re: str = r'href="([^"]+\.pdf)"'
    labels: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LABELS))
    stop_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class Profile:
    name: str
    path: Path
    buyers: dict[str, Buyer]
    region: Region = field(default_factory=Region)
    tribunal: Tribunal = field(default_factory=Tribunal)
    models: dict[str, str] = field(default_factory=dict)   # task -> model id, overrides provider defaults

    def buyer(self, key: str | None = None) -> Buyer:
        if key is None:
            return next(iter(self.buyers.values()))
        if key not in self.buyers:
            raise ProfileError(f"unknown buyer {key!r} in profile {self.name!r}",
                               f"the profile defines {', '.join(self.buyers)}",
                               f"use one of those keys, or add [buyers.{key}] to {self.path}")
        return self.buyers[key]


def _regex(value: str, where: str, path: Path) -> str:
    try:
        re.compile(value)
    except re.error as e:
        raise ProfileError(f"invalid regular expression in {where}", str(e), f"fix {where} in {path}") from e
    return value


def _template_regex(value: str, where: str, path: Path) -> str:
    """A regex template over {year} and {num}: validated once with sample values, stored unformatted."""
    try:
        re.compile(value.format(year=2000, num=1))
    except (re.error, KeyError, IndexError, ValueError) as e:
        raise ProfileError(f"invalid regular expression template in {where}", str(e),
                           f"fix {where} in {path}; only {{year}} and {{num}} may appear in braces") from e
    return value


def _strings(value: object, where: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ProfileError(f"{where} must be a list of strings", f"got {value!r}", f"fix {where} in {path}")
    return tuple(value)


def resolve_path(name: str | None = None) -> Path:
    name = name or os.environ.get("LICITA_PROFILE") or DEFAULT_PROFILE
    path = Path(name) if name.endswith(".toml") else PROFILES_DIR / f"{name}.toml"
    if not path.is_file():
        known = sorted(p.stem for p in PROFILES_DIR.glob("*.toml"))
        raise ProfileError(f"profile {name!r} not found", f"no file at {path}",
                           f"set LICITA_PROFILE to one of {known} or to a .toml path; "
                           f"copy profiles/example.toml to start a new one")
    return path


def parse(path: Path) -> Profile:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"cannot parse profile {path.name}", str(e), f"fix the TOML syntax in {path}") from e
    buyers_raw = raw.get("buyers") or {}
    if not buyers_raw:
        raise ProfileError(f"profile {path.name} has no buyers", "the [buyers.<KEY>] tables are missing",
                           f"add at least one [buyers.<KEY>] table with name and nif to {path}")
    buyers = {}
    for key, b in buyers_raw.items():
        missing = [f for f in ("name", "nif") if not b.get(f)]
        if missing:
            raise ProfileError(f"buyer {key!r} is incomplete", f"missing {', '.join(missing)}",
                               f"add them under [buyers.{key}] in {path}")
        buyers[key] = Buyer(key, b["name"], b["nif"], b.get("short") or key)
    r, t = raw.get("region") or {}, raw.get("tribunal") or {}
    region = Region(
        label=r.get("label", ""), nuts_prefix=r.get("nuts_prefix", ""),
        buyer_chain_re=_regex(r.get("buyer_chain_re", ""), "region.buyer_chain_re", path),
        central_buyer_re=_regex(r.get("central_buyer_re", ""), "region.central_buyer_re", path),
        health_terms=_strings(r.get("health_terms", []), "region.health_terms", path),
        boilerplate_terms=_strings(r.get("boilerplate_terms", []), "region.boilerplate_terms", path))
    labels = dict(DEFAULT_LABELS)
    labels.update({k: str(v) for k, v in (t.get("labels") or {}).items() if k in FIELD_KEYS})
    defaults = Tribunal()
    tribunal = Tribunal(
        name=t.get("name", ""), short=t.get("short", ""), base_url=t.get("base_url", ""),
        first_year=int(t["first_year"]) if t.get("first_year") is not None else None,
        year_url=str(t.get("year_url", defaults.year_url)), ruling_url=str(t.get("ruling_url", defaults.ruling_url)),
        number_re=_template_regex(str(t.get("number_re", defaults.number_re)), "tribunal.number_re", path),
        pdf_re=_template_regex(str(t.get("pdf_re", defaults.pdf_re)), "tribunal.pdf_re", path),
        labels=labels, stop_terms=_strings(t.get("stop_terms", []), "tribunal.stop_terms", path))
    models = raw.get("models") or {}
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in models.items()):
        raise ProfileError("[models] must map task names to model ids", f"got {models!r}",
                           f'write e.g. extract = "mistral-small-latest" under [models] in {path}')
    return Profile(raw.get("name") or path.stem, path, buyers, region, tribunal, dict(models))


@cache
def _load(path: Path) -> Profile:
    return parse(path)


def load_profile(name: str | None = None) -> Profile:
    """The active profile (LICITA_PROFILE, else profiles/example.toml). Parsed once per path."""
    return _load(resolve_path(name))


def main(argv: list[str] | None = None) -> int:
    """`python -m licita.profile [buyers]`: show the active profile, or list its buyer keys (for scripts)."""
    import sys
    args = sys.argv[1:] if argv is None else argv
    try:
        p = load_profile()
    except ProfileError as e:
        print(e, file=sys.stderr)
        return 2
    if args[:1] == ["buyers"]:
        print(" ".join(p.buyers))
        return 0
    print(f"profile  {p.name}  ({p.path})")
    print(f"region   {p.region.label or '-'}  nuts={p.region.nuts_prefix or '-'}")
    print(f"tribunal {p.tribunal.short or '-'}  {p.tribunal.base_url or '(no listing URL)'}")
    for b in p.buyers.values():
        print(f"buyer    {b.key:10} {b.nif}  {b.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
