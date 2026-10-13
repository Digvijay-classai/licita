"""Fetch the current tender documents (PCAP, PPT, ...) for the contracts in the fact sheets.

  python -m licita.memo.documents [--buyer HOSP_A] [--kinds legal,technical]

Documents are untrusted input: stored as bytes with a manifest (URL, size, SHA-256, content type), size
capped, never executed or opened by anything but the PDF text extractor, and kept out of git under
data/docs/. Text is extracted alongside for the clause linter. Polite: one request at a time, delay
between requests, resumable (existing files are skipped).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import duckdb
import httpx

from licita.config import DATA_DIR
from licita.memo.facts import OUT_DIR, SPINE, safe_name
from licita.net import client
from licita.profile import load_profile

DOCS_DIR = DATA_DIR / "docs"
MAX_BYTES = 50 * 1024 * 1024
DELAY_S = 1.5
DEFAULT_KINDS = ("legal", "technical")     # PCAP and PPT; "additional"/"general" are notices and minutes


def attachments(con, buyer_nif: str, folder_id: str, kinds: tuple[str, ...]) -> list[dict]:
    row = con.execute("select attachments_json from folders where buyer_nif = ? and folder_id = ?",
                      [buyer_nif, folder_id]).fetchone()
    docs = json.loads(row[0]) if row and row[0] else []
    return [d for d in docs if d.get("kind") in kinds and d.get("uri")]


def fetch_one(http: httpx.Client, doc: dict, dest: Path) -> dict:
    name = f"{doc['kind']}__{safe_name(doc.get('file') or doc['id'])}"
    rec = {"kind": doc["kind"], "id": doc["id"], "name": doc.get("file") or doc["id"], "uri": doc["uri"],
           "path": None, "status": None, "bytes": None, "sha256": None, "content_type": None,
           "fetched_at": datetime.now().isoformat(timespec="seconds")}
    existing = sorted(dest.glob(f"{name}.*"))
    existing = [p for p in existing if p.suffix != ".txt"]
    if existing:
        data = existing[0].read_bytes()
        return {**rec, "path": existing[0].name, "status": "cached", "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}
    try:
        with http.stream("GET", doc["uri"], follow_redirects=True) as r:
            if r.status_code != 200:
                return {**rec, "status": f"http_{r.status_code}"}
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    return {**rec, "status": "too_large", "content_type": ctype}
                chunks.append(chunk)
    except httpx.TransportError as e:
        return {**rec, "status": f"error: {type(e).__name__}"}
    data = b"".join(chunks)
    ext = ".pdf" if data.startswith(b"%PDF") else ".zip" if data.startswith(b"PK") else ".bin"
    if ext == ".bin" and ctype.startswith("text/html"):
        return {**rec, "status": "html_not_document", "content_type": ctype}
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{name}{ext}"
    path.write_bytes(data)
    return {**rec, "path": path.name, "status": "ok", "bytes": len(data), "content_type": ctype,
            "sha256": hashlib.sha256(data).hexdigest()}


def extract_texts(dest: Path) -> int:
    from licita.rulings.pdfs import extract_text
    n = 0
    for pdf in dest.glob("*.pdf"):
        txt = pdf.with_suffix(".txt")
        if txt.exists():
            continue
        try:
            text, _ = extract_text(pdf.read_bytes())
        except Exception as e:  # noqa: BLE001 (a malformed PDF must not stop the batch)
            text = ""
            print(f"  text extraction failed for {pdf.name}: {type(e).__name__}")
        txt.write_text(text)
        n += 1
    return n


def fetch_hospital(hospital: str, kinds: tuple[str, ...] = DEFAULT_KINDS, spine: Path = SPINE,
                   facts_dir: Path | None = None, docs_dir: Path = DOCS_DIR,
                   http: httpx.Client | None = None, delay: float = DELAY_S) -> dict:
    facts_dir = facts_dir or OUT_DIR / hospital / "facts"
    con = duckdb.connect(str(spine), read_only=True)
    nif = load_profile().buyer(hospital).nif
    totals: dict[str, int] = {}
    own_http = http is None
    http = http or client(timeout=60)
    try:
        for sheet in sorted(facts_dir.glob("*.json")):
            folder_id = json.loads(sheet.read_text())["contract"]["folder_id"]
            dest = docs_dir / hospital / safe_name(folder_id)
            recs = []
            for doc in attachments(con, nif, folder_id, kinds):
                rec = fetch_one(http, doc, dest)
                recs.append(rec)
                totals[rec["status"]] = totals.get(rec["status"], 0) + 1
                if rec["status"] != "cached":
                    time.sleep(delay)
            if recs:
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "manifest.json").write_text(json.dumps(
                    {"folder_id": folder_id, "hospital": hospital, "documents": recs}, ensure_ascii=False, indent=1))
                extract_texts(dest)
            print(f"{folder_id}: {[r['status'] for r in recs] or 'no documents listed'}", flush=True)
    finally:
        if own_http:
            http.close()
    print(totals)
    return totals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--buyer", "--hospital", dest="buyer", help="buyer key from the profile (default: the first)")
    ap.add_argument("--kinds", default=",".join(DEFAULT_KINDS))
    args = ap.parse_args(argv)
    fetch_hospital(load_profile().buyer(args.buyer).key, tuple(args.kinds.split(",")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
