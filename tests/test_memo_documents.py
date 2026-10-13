import json

import duckdb
import httpx
import pytest

from licita.memo import documents

PDF = b"%PDF-1.4 minimal"


def _transport(request: httpx.Request) -> httpx.Response:
    doc = request.url.params.get("DocumentIdParam")
    if doc == "pcap":
        return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    if doc == "html":
        return httpx.Response(200, content=b"<html>login</html>", headers={"content-type": "text/html"})
    if doc == "big":
        return httpx.Response(200, content=b"%PDF" + b"x" * 64)
    return httpx.Response(404)


@pytest.fixture
def env(tmp_path, monkeypatch):
    spine = tmp_path / "spine.duckdb"
    con = duckdb.connect(str(spine))
    con.execute("create table folders (buyer_nif varchar, folder_id varchar, attachments_json varchar)")
    base = "https://contrataciondelestado.es/doc?DocumentIdParam="
    atts = [{"kind": "legal", "id": "PCAP.pdf", "file": None, "uri": base + "pcap"},
            {"kind": "technical", "id": "PPT.pdf", "file": None, "uri": base + "missing"},
            {"kind": "technical", "id": "x", "file": "Anexo", "uri": base + "html"},
            {"kind": "general", "id": "acta", "file": "Acta mesa", "uri": base + "pcap"}]
    con.execute("insert into folders values ('Q0000001A', 'A/1/2025', ?)", [json.dumps(atts)])
    con.close()
    facts = tmp_path / "facts"
    facts.mkdir()
    (facts / "A_1_2025.json").write_text(json.dumps({"contract": {"folder_id": "A/1/2025"}}))
    monkeypatch.setattr(documents, "extract_texts", lambda dest: 0)
    http = httpx.Client(transport=httpx.MockTransport(_transport))
    return {"spine": spine, "facts_dir": facts, "docs_dir": tmp_path / "docs", "http": http, "delay": 0}


def test_fetch_stores_pdfs_with_manifest_and_skips_non_documents(env):
    totals = documents.fetch_hospital("HOSP_A", **env)
    assert totals == {"ok": 1, "http_404": 1, "html_not_document": 1}   # 'general' kind not requested
    dest = env["docs_dir"] / "HOSP_A" / "A_1_2025"
    man = json.loads((dest / "manifest.json").read_text())["documents"]
    ok = next(d for d in man if d["status"] == "ok")
    assert (dest / ok["path"]).read_bytes() == PDF and len(ok["sha256"]) == 64
    # second run is served from disk
    assert documents.fetch_hospital("HOSP_A", **env)["cached"] == 1


def test_oversized_documents_are_refused(env, monkeypatch):
    monkeypatch.setattr(documents, "MAX_BYTES", 10)
    rec = documents.fetch_one(env["http"], {"kind": "legal", "id": "b", "uri": "https://x/?DocumentIdParam=big"},
                              env["docs_dir"])
    assert rec["status"] == "too_large" and not env["docs_dir"].exists()
