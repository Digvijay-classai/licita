"""Download tribunal ruling PDFs and extract their text (resumable, polite).

  python -m licita.rulings.pdfs [--only-pliegos]

Text goes into rulings.duckdb (table ruling_texts). Scanned PDFs without a text layer are marked
`no_text` so a later OCR pass can pick them up; nothing is silently dropped.
"""
from __future__ import annotations

import argparse
import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import duckdb
import httpx
import pdfplumber

from licita.config import DATA_DIR
from licita.net import client
from licita.rulings.classify import challenges_pliegos
from licita.rulings.db import DB, connect, split_id

PDF_DIR = DATA_DIR / "rulings_pdf"
MIN_TEXT_CHARS = 500


def _db() -> duckdb.DuckDBPyConnection:
    return connect(DB)


def extract_text(pdf_bytes: bytes) -> tuple[str, int]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [p.extract_text() or "" for p in pdf.pages]
    return "\n\n".join(pages), len(pages)


def fetch(rid: str, url: str) -> dict:
    base = {"id": rid, "fetched_at": datetime.now()}
    source, num, year = split_id(rid)
    path = PDF_DIR / (f"{year}_{num}.pdf" if source is None else f"{source}_{year}_{num}.pdf")
    legacy = PDF_DIR / f"{year}_{num}.pdf"     # files downloaded before ids were source-qualified
    if not path.exists() and legacy.exists():
        legacy.rename(path)
    try:
        if not path.exists():
            with client(timeout=60) as c:
                r = c.get(url)
            time.sleep(1.0)
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                return {**base, "status": "failed", "error": f"HTTP {r.status_code} / not a PDF"}
            PDF_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        text, pages = extract_text(path.read_bytes())
    except httpx.TransportError as e:
        return {**base, "status": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
    except (pdfplumber.pdfminer.pdfparser.PDFSyntaxError, ValueError) as e:  # corrupt PDF
        return {**base, "status": "failed", "error": f"pdf: {e}"[:300]}
    status = "done" if len(text) >= MIN_TEXT_CHARS else "no_text"
    return {**base, "status": status, "pages": pages, "chars": len(text), "text": text, "text_source": "pdfplumber"}


def ocr_missing(con, provider=None, limit: int | None = None) -> dict:
    """Re-read PDFs whose embedded text is missing or too short with Mistral OCR (scanned rulings).

    Bytes are sent inline; the result is stored with text_source = 'mistral-ocr' so evaluations can tell
    OCR text from embedded text and quotes are verified against the exact text the model saw.
    """
    from licita.llm import Task, get_provider
    provider = provider or get_provider("mistral")
    rows = con.execute("select id from ruling_texts where status = 'no_text' order by id").fetchall()
    rows = rows[:limit] if limit else rows
    totals = {"ocr_done": 0, "ocr_failed": 0, "no_pdf": 0}
    model = provider.resolve_model(provider.model_for(Task.OCR))
    for (rid,) in rows:
        source, num, year = split_id(rid)
        path = PDF_DIR / (f"{year}_{num}.pdf" if source is None else f"{source}_{year}_{num}.pdf")
        if not path.exists():
            totals["no_pdf"] += 1
            continue
        try:
            text, pages = provider.ocr(path.read_bytes(), model)
        except Exception as e:   # noqa: BLE001 - one bad scan must not stop the run; recorded per row
            con.execute("update ruling_texts set error = ? where id = ?", [f"ocr: {e}"[:300], rid])
            totals["ocr_failed"] += 1
            continue
        status = "done" if len(text) >= MIN_TEXT_CHARS else "no_text"
        con.execute("update ruling_texts set status = ?, text = ?, chars = ?, pages = ?, text_source = ? where id = ?",
                    [status, text, len(text), pages, f"mistral-ocr:{model}", rid])
        totals["ocr_done"] += int(status == "done")
    print(totals)
    return totals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-pliegos", action="store_true", help="only rulings on tender documents")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--ocr", action="store_true", help="after fetching, OCR scanned PDFs with Mistral (needs key)")
    ap.add_argument("--ocr-limit", type=int)
    args = ap.parse_args(argv)
    con = _db()
    rows = con.execute("""select r.id, r.pdf_url, r.acto from rulings r
                          left join ruling_texts t using (id)
                          where r.status = 'done' and r.pdf_url is not null
                            and (t.status is null or t.status = 'failed')""").fetchall()
    todo = [(rid, url) for rid, url, acto in rows if not args.only_pliegos or challenges_pliegos(acto)]
    print(f"{len(todo)} PDFs to fetch", flush=True)
    cols = ["id", "status", "error", "pages", "chars", "text", "fetched_at", "text_source"]
    with ThreadPoolExecutor(args.workers) as ex:
        for i, rec in enumerate(ex.map(lambda t: fetch(*t), todo), 1):
            con.execute(f"insert or replace into ruling_texts ({','.join(cols)}) values ({','.join('?' * len(cols))})",
                        [rec.get(k) for k in cols])
            if i % 100 == 0:
                print(f"{i}/{len(todo)}", flush=True)
    print(con.execute("select status, count(*), sum(chars) from ruling_texts group by 1").fetchall())
    if args.ocr:
        ocr_missing(con, limit=args.ocr_limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
