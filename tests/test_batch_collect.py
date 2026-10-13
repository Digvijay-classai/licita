"""End-to-end submit -> collect against a fake Mistral batch provider replaying the recorded-shape fixtures."""
import json
from pathlib import Path

import pytest

from licita.llm.base import BatchState, BatchStatus, Result, parse_batch_line
from licita.rulings import extract

FIX = Path(__file__).parent / "fixtures" / "mistral_batch"
TEXT = 'Fundamento tercero. El PPT exige "bombas de infusión de la marca Acme modelo Z-200" sin admitir equivalentes.'
GOOD = {"expediente": "A/1", "contract_object": "Lentes", "contracting_body": "Hospital A", "challenged_act": "pliegos",
        "outcome": "upheld", "grounds": [{"ground": "brand_or_tailored_specs", "document": "PPT",
                                          "challenged_clause_quote": "bombas de infusión de la marca Acme modelo Z-200",
                                          "articles": ["126"], "holding": "upheld", "holding_summary_es": "Marca."}]}
IDS = [f"tre:{n}/2024" for n in range(1, 7)]   # 6 is never answered


class FakeBatch:
    name = "mistral"

    def __init__(self, state=BatchState.SUCCESS):
        self.state, self.submitted, self.cleaned = state, None, False

    def model_for(self, task):
        return "mistral-small-latest"

    def resolve_model(self, model):
        return "mistral-small-2603"

    def complete(self, *a, **k):
        raise AssertionError("batch path must not call complete")

    def submit_batch(self, requests, model, metadata=None):
        self.submitted = (requests, model, metadata)
        return BatchStatus("job-1", BatchState.QUEUED, total=len(requests))

    def batch_status(self, job_id):
        return BatchStatus(job_id, self.state, total=6, output_file="out", error_file="err")

    def batch_lines(self, status):
        out = (FIX / "output.jsonl").read_text().replace("__GOOD__", json.dumps(GOOD).replace('"', '\\"'))
        for raw in out.splitlines() + (FIX / "error.jsonl").read_text().splitlines():
            try:
                yield parse_batch_line(raw)
            except json.JSONDecodeError:
                from licita.llm.base import BatchLine
                yield BatchLine("", None, None, "unparseable")

    def cleanup(self, status):
        self.cleaned = True


@pytest.fixture
def con(tmp_path):
    c = extract._db(tmp_path / "r.duckdb")
    for i, rid in enumerate(IDS, 1):
        c.execute("insert into rulings (id, year, num, acto) values (?, 2024, ?, 'Pliegos')", [rid, i])
        c.execute("insert into ruling_texts (id, status, text) values (?, 'done', ?)", [rid, TEXT])
    return c


def test_submit_pins_model_records_membership_and_sends_no_tools(con):
    fake = FakeBatch()
    out = extract.submit("pliegos", None, provider=fake, con=con)
    reqs, model, meta = fake.submitted
    assert out["submitted"] == 6 and model == "mistral-small-2603" and meta["prompt_hash"] == extract.PROMPT_HASH
    assert all("tools" not in body and "model" not in body for _, body in reqs)   # model is set on the job
    assert {cid for cid, _ in reqs} == set(IDS)      # custom_id is the db id verbatim, ':' and '/' included
    assert con.execute("select count(*) from batch_requests where batch_id = 'job-1'").fetchone()[0] == 6


def test_collect_survives_every_bad_line_and_requeues_the_missing(con):
    fake = FakeBatch()
    extract.submit("pliegos", None, provider=fake, con=con)
    totals = extract.collect(provider=fake, con=con)
    st = dict(con.execute("select id, status from ruling_extractions").fetchall())
    assert st == {"tre:1/2024": "done", "tre:2/2024": "invalid_json", "tre:3/2024": "length",
                  "tre:4/2024": "errored", "tre:5/2024": "invalid", "tre:6/2024": "missing"}
    assert totals["duplicate_line"] == 1 and totals["unknown_id"] == 2 and totals["missing"] == 1
    assert fake.cleaned
    batch = con.execute("select collected_at is not null, n_succeeded, n_missing, state from extraction_batches").fetchone()
    assert batch == (True, 1, 1, "SUCCESS")
    row = con.execute("select provider, prompt_hash, batch, input_tokens, text_source from ruling_extractions "
                      "where id = 'tre:1/2024'").fetchone()
    assert row == ("mistral", extract.PROMPT_HASH, True, 1200, "pdfplumber")
    assert con.execute("select quote_verified from ruling_grounds where id = 'tre:1/2024'").fetchone() == (True,)
    # transport failures and unanswered requests are retried; model failures stay as evidence
    again = {rid for rid, _ in extract.pending(con, "pliegos", "mistral-small-2603")}
    assert again == {"tre:4/2024", "tre:6/2024"}


def test_collect_is_idempotent_and_waits_for_running_jobs(con):
    fake = FakeBatch(state=BatchState.RUNNING)
    extract.submit("pliegos", None, provider=fake, con=con)
    assert extract.collect(provider=fake, con=con)["pending_batches"] == 1
    assert con.execute("select count(*) from ruling_extractions").fetchone()[0] == 0
    fake.state = BatchState.SUCCESS
    extract.collect(provider=fake, con=con)
    assert extract.collect(provider=fake, con=con) == {"pending_batches": 0}     # nothing left open


def test_second_submit_before_collect_does_not_resend_open_requests(con):
    fake = FakeBatch(state=BatchState.RUNNING)
    extract.submit("pliegos", 2, provider=fake, con=con)
    first = {cid for cid, _ in fake.submitted[0]}
    assert extract.pending(con, "pliegos", "mistral-small-2603") and not (
        first & {rid for rid, _ in extract.pending(con, "pliegos", "mistral-small-2603")})
    fake.state = BatchState.SUCCESS
    fake.batch_lines = lambda status: iter(())
    extract.collect(provider=fake, con=con)          # collected (all missing) -> retryable again
    assert first <= {rid for rid, _ in extract.pending(con, "pliegos", "mistral-small-2603")}


def test_failed_job_marks_everything_missing_and_retryable(con):
    fake = FakeBatch(state=BatchState.TIMEOUT_EXCEEDED)
    fake.batch_lines = lambda status: iter(())
    extract.submit("pliegos", 2, provider=fake, con=con)
    extract.collect(provider=fake, con=con)
    assert {s for (s,) in con.execute("select status from ruling_extractions").fetchall()} == {"missing"}
    assert len(extract.pending(con, "pliegos", "mistral-small-2603")) == 6


def test_sync_path_for_local_servers(con):
    class Local:
        name = "local"

        def model_for(self, task):
            return "mistralai/Mistral-Small-3.2-24B-Instruct-2506"

        def resolve_model(self, model):
            return model

        def complete(self, messages, model, schema=None, **kw):
            assert schema is extract.SCHEMA and messages[0]["role"] == "system"
            return Result(json.dumps(GOOD), "stop", model, 10, 5, 0.2, 1)

    out = extract.submit("pliegos", 3, provider=Local(), con=con)
    assert out == {"done": 3, "model": "mistralai/Mistral-Small-3.2-24B-Instruct-2506"}
    assert con.execute("select distinct provider, batch from ruling_extractions").fetchall() == [("local", False)]
