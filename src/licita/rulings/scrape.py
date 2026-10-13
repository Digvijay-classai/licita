"""Scrape a procurement tribunal's published rulings into a resumable jobs table.

Everything that describes one tribunal's website lives in the profile's `[tribunal]` section: the listing
URL, the URL templates of a year's listing and of one ruling, the regex that reads ruling numbers off the
year listing, the regex that finds the PDF link, the labels of the structured fields on a ruling page and
the words that end a field. This module knows only the shape: rulings numbered 1..N per year, one page per
ruling with labelled fields and a PDF. Polite: 2 workers, about one request per second overall.

  python -m licita.rulings.scrape [--years FIRST LAST]
"""
from __future__ import annotations

import argparse
import html as H
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urljoin

import duckdb
import httpx

from licita.errors import LicitaError
from licita.net import client
from licita.profile import Tribunal, load_profile
from licita.rulings.db import DB, connect, ruling_id, source_key


def tribunal() -> Tribunal:
    p = load_profile()
    if not p.tribunal.base_url:
        raise LicitaError("no tribunal listing URL configured", f"profile {p.name!r} has an empty tribunal.base_url",
                          f"set [tribunal] base_url in {p.path}")
    return p.tribunal


def base_url() -> str:
    return tribunal().base_url.rstrip("/")


def _db() -> duckdb.DuckDBPyConnection:
    return connect(DB)


def check_source(con) -> str:
    """Refuse to mix tribunals by accident: the active profile's source must match the database's."""
    src = source_key()
    known = [s for (s,) in con.execute("select distinct source from rulings where source is not null").fetchall()]
    if known and src not in known:
        p = load_profile()
        raise LicitaError(f"profile {p.name!r} (source {src!r}) does not match this rulings database",
                          f"the database holds rulings from {', '.join(known)}",
                          "set LICITA_PROFILE to the profile that built it, or point LICITA_DATA at another folder "
                          "to start a database for a new tribunal")
    return src


def _text(html: str) -> str:
    return H.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))


def year_url(t: Tribunal, year: int) -> str:
    return t.year_url.format(base=t.base_url.rstrip("/"), year=year)


def ruling_url(t: Tribunal, year: int, num: int) -> str:
    return t.ruling_url.format(base=t.base_url.rstrip("/"), year=year, num=num)


def max_number(c: httpx.Client, year: int, t: Tribunal | None = None) -> int:
    t = t or tribunal()
    page = c.get(year_url(t, year)).text
    nums = [int(n) for n in re.findall(t.number_re.format(year=year), page)]
    return max(nums) if nums else 0


def parse_page(html: str, year: int, num: int, t: Tribunal | None = None) -> dict:
    """The labelled fields of one ruling page, order-independent, plus the absolute PDF URL when linked."""
    t = t or tribunal()
    txt = _text(html)
    stop = "(?=" + "|".join(re.escape(s) for s in (*t.labels.values(), *t.stop_terms)) + "|$)"
    rec = {k: ((m.group(1).strip() or None) if (m := re.search(re.escape(lbl) + r"\s*(.*?)\s*" + stop, txt)) else None)
           for k, lbl in t.labels.items()}
    pdf = re.search(t.pdf_re.format(year=year, num=num), html)
    rec["pdf_url"] = urljoin(ruling_url(t, year, num), pdf.group(1)) if pdf else None
    rec["summary"] = None   # full text comes from the PDF (rulings.extract)
    return rec


def fetch_one(year: int, num: int) -> dict:
    t = tribunal()
    rid = ruling_id(num, year)
    url = ruling_url(t, year, num)
    base = {"id": rid, "source": source_key(), "year": year, "num": num, "url": url, "fetched_at": datetime.now()}
    with client(timeout=30) as c:
        try:
            r = c.get(url)
        except httpx.TransportError as e:
            return {**base, "status": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
    time.sleep(1.0)
    if r.status_code == 404:
        return {**base, "status": "missing"}
    if r.status_code != 200:
        return {**base, "status": "failed", "error": f"HTTP {r.status_code}"}
    rec = parse_page(r.text, year, num, t)
    return {**base, **rec, "status": "done" if rec.get("organo") else "unparsed"}


def main(argv: list[str] | None = None) -> int:
    t = tribunal()
    if t.first_year is None:
        p = load_profile()
        raise LicitaError("no first year to scrape", f"profile {p.name!r} sets no tribunal.first_year",
                          f"set [tribunal] first_year in {p.path} (the first year the tribunal publishes online)")
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs=2, type=int, default=[t.first_year, datetime.now().year])
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args(argv)
    con = _db()
    check_source(con)
    done = {r[0] for r in con.execute("select id from rulings where status in ('done','missing')").fetchall()}
    todo = []
    with client() as c:
        for y in range(args.years[0], args.years[1] + 1):
            n = max_number(c, y, t)
            todo += [(y, k) for k in range(1, n + 1) if ruling_id(k, y) not in done]
            print(f"{y}: {n} rulings", flush=True)
    print(f"{len(todo)} to fetch", flush=True)
    cols = ["id", "source", "year", "num", "url", "status", "error", "fecha", "organo", "acto", "objeto",
            "tipo_contrato", "tipo_resolucion", "summary", "pdf_url", "fetched_at"]
    with ThreadPoolExecutor(args.workers) as ex:
        for i, rec in enumerate(ex.map(lambda yk: fetch_one(*yk), todo), 1):
            con.execute(f"insert or replace into rulings ({','.join(cols)}) values ({','.join('?' * len(cols))})",
                        [rec.get(k) for k in cols])
            if i % 100 == 0:
                print(f"{i}/{len(todo)}", flush=True)
    print(con.execute("select status, count(*) from rulings group by 1").fetchall())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
